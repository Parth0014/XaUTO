from playwright.sync_api import sync_playwright

def login_x():

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=False
        )

        context = browser.new_context()

        page = context.new_page()

        page.goto("https://x.com/login")

        print("Login manually in browser...")

        input("Press ENTER after login completed...")

        context.storage_state(
            path="app/scraper/session/x_login.json"
        )

        print("Login session saved!")

        browser.close()


if __name__ == "__main__":
    login_x()