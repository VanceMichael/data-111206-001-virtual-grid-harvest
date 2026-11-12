import json
import unittest
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
from browser_source import first_screen
from portal import serve


class BrowserTests(unittest.TestCase):
    def test_first_screen_is_not_full_export(self):
        with serve() as base:
            result = first_screen(base)
        self.assertEqual(len(result['rows']), 5)
        self.assertEqual(result['declared_total'], 17)

    def test_dom_node_is_reused(self):
        with serve() as base, sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(base)
                page.locator('#viewport[data-state="ready"]').wait_for()
                first = page.locator('#recycled-0').locator('button').get_attribute('data-reference')
                page.locator('#viewport').evaluate('(node) => {node.scrollTop=520;}')
                page.wait_for_function('document.querySelector("#recycled-0").dataset.rowIndex === "10"')
                later = page.locator('#recycled-0').locator('button').get_attribute('data-reference')
                self.assertNotEqual(first, later)
                page.locator('#recycled-0 button').click()
                self.assertEqual(page.locator('#detail').get_attribute('data-reference'), later)
            finally:
                browser.close()

    def test_source_change(self):
        with serve() as base:
            with urlopen(base+'/advance') as response:
                data = json.load(response)
        self.assertEqual(data['revision'],2)
        self.assertEqual(len(data['rows']),18)
