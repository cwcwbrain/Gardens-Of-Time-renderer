"""Numeric background copies retain their authored background classification."""
import unittest

from gardens.render import is_background


class BackgroundNamesTests(unittest.TestCase):
    def test_numbered_background_copies_and_existing_names_are_recognized(self):
        for name in ('bg02_1','BG02_12','bg7_1_2','bg02','bg_02','BG','background','backdrop'):
            with self.subTest(name=name):self.assertTrue(is_background(name))

    def test_unknown_names_and_non_numeric_suffixes_are_not_discarded_as_background(self):
        for name in ('bg02_hat','bg02_','bg02_1thing','abg02_1','background1','hat','coconutdrink\u00a0','firepit\u00a0'):
            with self.subTest(name=name):self.assertFalse(is_background(name))


if __name__=='__main__':unittest.main()
