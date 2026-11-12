from playwright.sync_api import sync_playwright


def first_screen(url):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 720})
            page.goto(url, wait_until='domcontentloaded')
            page.locator('#viewport[data-state="ready"]').wait_for()
            rows = page.locator('.row:not([hidden])')
            return {'revision': page.locator('#viewport').get_attribute('data-revision'),
                    'declared_total': int(page.locator('#viewport').get_attribute('data-total')),
                    'rows': [{'index': rows.nth(index).get_attribute('data-row-index'),
                              'text': rows.nth(index).inner_text(),
                              'reference': rows.nth(index).locator('button').get_attribute('data-reference')}
                             for index in range(rows.count())]}
        finally:
            browser.close()
