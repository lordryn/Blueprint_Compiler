from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time


def pull_fabricator_blueprints():
    url = "https://scmdb.net/?page=fab"
    output_filename = "blueprints unprocessed.txt"

    print("Starting headless browser to load dynamic content...")

    # Setup Chrome options so it runs in the background silently
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run without a visible window
    chrome_options.add_argument("--log-level=3")  # Suppress unnecessary terminal logs

    # Initialize the Chrome driver automatically
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        print(f"Loading {url}...")
        driver.get(url)

        # Wait up to 15 seconds for AT LEAST one element with class 'fabricator-main' to appear
        print("Waiting for Javascript to populate data...")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "fabricator-main"))
        )

        # Give it a small buffer just in case the remaining elements are still rendering
        time.sleep(2)

        # Now that JS has loaded the content, grab the fully rendered HTML
        page_source = driver.page_source

    except Exception as e:
        print(f"Timed out or encountered an error waiting for elements to load: {e}")
        return
    finally:
        # Always close the browser instance to prevent memory leaks
        driver.quit()

        # Pass the fully loaded HTML to BeautifulSoup
    soup = BeautifulSoup(page_source, 'html.parser')
    fabricator_divs = soup.find_all('div', class_='fabricator-main')

    if not fabricator_divs:
        print("No elements found. The class name might have changed or the site is blocking automated access.")
        return

    # Write the compiled elements to the text file
    with open(output_filename, 'w', encoding='utf-8') as file:
        for i, div in enumerate(fabricator_divs, start=1):
            file.write(str(div))

    print(f"Successfully pulled {len(fabricator_divs)} elements and saved them to '{output_filename}'.")


if __name__ == "__main__":
    pull_fabricator_blueprints()