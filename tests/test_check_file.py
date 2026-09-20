#!/usr/bin/env python3
"""Tests for scripts/check_file.should_back_up against real drive-list paths."""

import unittest

from scripts.check_file import should_back_up

# Real content worth backing up, pulled from p-drive-list.txt / q-drive-list.txt.
KEEP_PATHS = [
    r"P:\1-31-20 Dummy.pdf",
    r"P:\Archive Papers\2009\8 Pages.indd",
    r"P:\Archive Papers\2009\01.20.09\Pdfs\cover.jpg",
    r"P:\Archive Papers\2009\01.21.09\news\Inauguration.DONOTUSE\audio.mp3",
    r"P:\Archive Papers\2009\04.06.09\news\ASUC Forum\Slide Questions AAVP.psd",
    r"P:\Design\02. Design Tests\OLD\Spring 2016\2.8.16.news.lauren.icml",
    r"P:\Editorial Depository\Archives\2015\10.1.15\Dummy\10-1-15 Dummy Cover.xlsx",
    r"Q:\DailyCal05new-2022 (May 29-2022).QBW",
    r"P:\News\christine.assignment",  # staffer-name "extension", still real content
]

# Junk/cache/lock files, pulled from the same drive lists where possible.
EXCLUDED_PATHS = [
    (r"P:\Archive Papers\2009\01.20.09\01.20.09\news\~asuc~-x5w18.idlk", "InDesign lock file"),
    (r"P:\Finance.ND", "QuickBooks network descriptor"),
    (r"Q:\DailyCal05new-2022 (May 29-2022).QBW.TLG", "QuickBooks transaction log"),
    (r"P:\Finance.DSN", "QuickBooks data source name file"),
    (r"Q:\DailyCal05new-2022 (May 29-2022).SDS", "QuickBooks search data file"),
    (r"Q:\DailyCal05new-2022.QBW.SearchIndex", "QuickBooks search cache"),
    (r"P:\Old Folders\recovered.chk", "recovered file fragment"),
    (r"P:\Branding\Acr179292615776192656613.tmp", "temporary file"),
    (r"P:\Archive Papers\2009\01.26.09\arts\AUDIO\noble.aup.bak", "backup copy of a file"),
    (
        r"Q:\Accounting\backup\QuickBooks Premier - Nonprofit Edition\Components\DownloadQB12\NewFeatures\.QBInstall.old",
        "old renamed copy",
    ),
    (r"P:\Arts\Arts\Arts 02 08\2-11 Content.zip.download", "incomplete browser download"),
    (
        r"P:\Design\Y. Executive\Z. Editors Folders\Caragh\Color Palettes\2017_SpecialIssue_ColorPalettes-01.png.crdownload",
        "incomplete Chrome download",
    ),
    (r"P:\3D Objects - Shortcut.lnk", "Windows shortcut pointer"),
    (r"P:\Branding\00. Official Assets\Colors\DC Web Colors.webloc", "Mac internet shortcut"),
    (r"P:\Archive Papers\2009\01.30.09\dailycal.1.30.09.page1.Cover.Thumbnail", "cached image thumbnail"),
    (r"P:\Archive Papers\2010\10.11.10\PDFs\Small PDFs\hermes.thumb", "cached image thumbnail"),
    (r"P:\Some Folder\.DS_Store", "Finder folder metadata cache"),
    (r"Q:\assert.dmp", "application crash dump"),
    (r"Q:\DC Alum\debug.log", "application debug log"),
    (r"P:\Design\Y. Executive\Z. Editors Folders\Old Editors\Kristie\CDA Logo Contest.pdf alias", "Mac alias file"),
    (r"P:\Design\Y. Executive\Z. Editors Folders\Old Editors\Kristie\cheapink.sit alias", "Mac alias file"),
    (r"P:\Design\Y. Executive\Z. Editors Folders\Old Editors\Kristie\bodonixt.hqx alias 2", "Mac alias file"),
]

# Copies of installed applications, pulled from q-drive-list.txt / p-drive-list.txt.
EXCLUDED_APP_PATHS = [
    r"P:\Copy\Stuff on desktop\Firefox.app\Contents\MacOS\firefox",
    r"P:\Copy\Stuff on desktop\Firefox.app\Contents\MacOS\components\nsProxyAutoConfig.js",
    r"Q:\Accounting\backup\QuickBooks Premier - Nonprofit Edition\Components\Payroll\Cps\JRE\bin\java.exe",
    r"Q:\AcctDepo\OLD FILES\Archive\Files From Ganymede\backup\QuickBooks Premier - Nonprofit Edition\Components\Services\QBUpdate.exe",
    r"Q:\Accounting\RAPID\BeneIn.exe",
    r"Q:\AcctDepo\OLD FILES\RAPID\INTRAN\JAVA\bin\java.exe",
]


class KeepFilesTests(unittest.TestCase):
    def test_real_content_is_kept(self):
        for path in KEEP_PATHS:
            with self.subTest(path=path):
                self.assertTrue(should_back_up(path))


class ExcludedJunkFilesTests(unittest.TestCase):
    def test_junk_and_cache_files_are_excluded(self):
        for path, reason in EXCLUDED_PATHS:
            with self.subTest(path=path, reason=reason):
                self.assertFalse(should_back_up(path))


class ExcludedApplicationCopiesTests(unittest.TestCase):
    def test_application_copies_are_excluded(self):
        for path in EXCLUDED_APP_PATHS:
            with self.subTest(path=path):
                self.assertFalse(should_back_up(path))

    def test_the_app_folder_itself_is_excluded(self):
        self.assertFalse(should_back_up(r"P:\Copy\Stuff on desktop\Firefox.app"))
        self.assertFalse(
            should_back_up(r"Q:\Accounting\backup\QuickBooks Premier - Nonprofit Edition")
        )
        self.assertFalse(should_back_up(r"Q:\Accounting\RAPID"))


class EdgeCaseTests(unittest.TestCase):
    def test_matching_is_case_insensitive(self):
        self.assertFalse(should_back_up(r"P:\News\LAYOUT.IDLK"))
        self.assertFalse(should_back_up(r"Q:\ACCOUNTING\RAPID\beneout.exe"))
        self.assertFalse(should_back_up(r"Q:\ASSERT.DMP"))

    def test_forward_slash_paths_are_handled(self):
        self.assertFalse(should_back_up("Q:/Accounting/RAPID/BeneIn.exe"))
        self.assertTrue(should_back_up("P:/News/story.indd"))

    def test_bare_filename_with_no_path(self):
        self.assertFalse(should_back_up("layout.idlk"))
        self.assertTrue(should_back_up("story.indd"))

    def test_extension_substring_does_not_false_positive(self):
        # "rapidly.txt" contains "rapid" but is not the RAPID application folder.
        self.assertTrue(should_back_up(r"P:\News\rapidly.txt"))
        # A folder literally named "old" is not the ".old" file extension.
        self.assertTrue(should_back_up(r"P:\Old Folders\Reports\budget.xlsx"))


if __name__ == "__main__":
    unittest.main()
