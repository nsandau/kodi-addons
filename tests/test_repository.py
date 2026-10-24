import hashlib
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile

from scripts.build_release import ROOT
from scripts.build_repository import build_repository


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs)["href"])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.site = build_repository(Path(self.temporary.name) / "site")
        self.index = ElementTree.parse(self.site / "addons.xml").getroot()
        self.repo = self.index.find("addon[@id='repository.nsandau.kodi']")
        self.directory = self.repo.find("extension[@point='xbmc.addon.repository']/dir")
        self.base = self.directory.findtext("info").rsplit("/", 1)[0] + "/"

    def hosted_path(self, url):
        self.assertTrue(url.startswith(self.base))
        return self.site / url[len(self.base) :]

    def test_metadata_matches_canonical_manifests_and_packages(self):
        self.assertEqual(len(self.index), 2)
        canonical_paths = (ROOT / "addon.xml", ROOT / "repository/addon.xml")
        for addon, canonical_path in zip(self.index, canonical_paths, strict=True):
            canonical = ElementTree.parse(canonical_path).getroot()
            self.assertEqual(
                ElementTree.tostring(addon), ElementTree.tostring(canonical)
            )
            addon_id, version = addon.attrib["id"], addon.attrib["version"]
            package = self.hosted_path(
                self.directory.findtext("datadir")
                + addon_id
                + "/"
                + addon_id
                + "-"
                + version
                + ".zip"
            )
            with ZipFile(package) as archive:
                self.assertIsNone(archive.testzip())
                manifest = ElementTree.fromstring(archive.read(addon_id + "/addon.xml"))
                self.assertEqual(manifest.attrib, addon.attrib)
                self.assertFalse(
                    any(
                        name.endswith("/config.json") or ".git/" in name
                        for name in archive.namelist()
                    )
                )

    def test_repository_identity_is_generic_and_service_identity_is_unchanged(self):
        self.assertEqual(self.repo.attrib["name"], "nsandau's Kodi Addons")
        service = self.index.find("addon[@id='service.kodi.watchedcleanup']")
        self.assertEqual(service.attrib["name"], "Watched Media Cleanup")
        html = (self.site / "index.html").read_text()
        self.assertIn("nsandau&#x27;s Kodi Addons", html)
        self.assertIn("https://github.com/nsandau/kodi-addons#readme", html)
        self.assertNotIn("kodi-watched-cleanup", html)

    def test_repository_urls_and_change_token(self):
        self.assertEqual(urlsplit(self.base).scheme, "https")
        self.assertEqual(self.base, "https://nsandau.github.io/kodi-addons/")
        metadata = self.hosted_path(self.directory.findtext("info")).read_bytes()
        token = (
            self.hosted_path(self.directory.findtext("checksum")).read_text().strip()
        )
        self.assertEqual(token, hashlib.sha256(metadata).hexdigest())
        self.assertEqual(self.directory.findtext("hashes"), "false")
        self.assertTrue((self.site / ".nojekyll").exists())

    def test_kodi_source_links_resolve_and_include_repository_zip(self):
        bootstrap_links = None
        for path in self.site.rglob("index.html"):
            parser = LinkParser()
            parser.feed(path.read_text())
            if path.parent == self.site:
                bootstrap_links = parser.links
            for link in parser.links:
                if urlsplit(link).scheme:
                    continue
                target = path.parent / link
                self.assertTrue(target.exists(), link)
                if link.endswith("/"):
                    self.assertTrue((target / "index.html").is_file())
        self.assertTrue(
            any(
                link.startswith("addons/repository.") and link.endswith(".zip")
                for link in bootstrap_links
            )
        )

    def test_source_listing_matches_kodi_http_directory_parser(self):
        # Kodi Omega CHTTPDirectory requires a link label matching its relative
        # href and some trailing metadata after </a>. A pretty filename for a
        # nested href is silently hidden, even though it works in a web browser.
        expression = re.compile(
            r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)', re.MULTILINE
        )
        matches = expression.findall((self.site / "index.html").read_text())
        accepted = [
            href
            for href, label, _ in matches
            if href.rstrip("/") == label.strip().rstrip("/")
        ]
        packages = [href for href in accepted if href.endswith(".zip")]
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].startswith("addons/repository."))

    def test_packages_and_metadata_are_reproducible(self):
        other = build_repository(Path(self.temporary.name) / "other")
        for path in self.site.rglob("*"):
            if path.is_file():
                self.assertEqual(
                    path.read_bytes(),
                    (other / path.relative_to(self.site)).read_bytes(),
                )

    def test_nonempty_output_is_rejected_without_publishing_it(self):
        private = self.site / "config.json"
        private.write_text("private")
        with self.assertRaises(ValueError):
            build_repository(self.site)
        self.assertEqual(private.read_text(), "private")


if __name__ == "__main__":
    unittest.main()
