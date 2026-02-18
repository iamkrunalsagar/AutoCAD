import csv
import os
import sys
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# -----------------------------
# Configuration
# -----------------------------
SEARCH_URL = (
    "https://www.linkedin.com/search/results/people/?origin=FACETED_SEARCH"
    "&geoUrn=%5B%22106300413%22%2C%2290009626%22%2C%22100811329%22%2C%2290009633%22%5D"
    "&activelyHiringForJobTitles=%5B%22-100%22%2C%229%22%2C%22434%22%2C%2225201%22%2C%2298%22%2C%229625%22%5D"
    "&title=%22Founder%22&industry=%5B%2296%22%2C%2211%22%2C%221594%22%2C%224%22%2C%22118%22%5D"
)
CHROME_USER_DATA_DIR = r"C:\Users\kruna\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE_DIRECTORY = "Profile 7"
OUTPUT_CSV_PATH = r"C:\Users\kruna\OneDrive\Desktop\linkedin_profiles.csv"
MAX_PAGES = 100
SCROLL_ROUNDS_PER_PAGE = 8
WAIT_SECONDS = 20


# -----------------------------
# Utility Functions
# -----------------------------
def is_valid_linkedin_profile_url(url: str) -> bool:
    """Return True only for clean LinkedIn profile URLs that contain /in/."""
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if "linkedin.com" not in host:
        return False
    if "/in/" not in path:
        return False
    if "?" in url:
        url = url.split("?", 1)[0]

    return True


def normalize_profile_url(url: str) -> str:
    """Normalize LinkedIn profile URLs so deduplication is reliable."""
    if not url:
        return ""
    cleaned = url.split("?", 1)[0].strip()
    return cleaned.rstrip("/")


def setup_driver() -> webdriver.Chrome:
    """Create and return a Chrome WebDriver attached to an existing Chrome profile."""
    print("[1/8] Configuring Chrome options and attaching existing profile...")
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={CHROME_USER_DATA_DIR}")
    options.add_argument(f"--profile-directory={CHROME_PROFILE_DIRECTORY}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    print("[2/8] Installing/locating compatible ChromeDriver via ChromeDriverManager...")
    service = Service(ChromeDriverManager().install())

    print("[3/8] Launching Chrome WebDriver...")
    return webdriver.Chrome(service=service, options=options)


def wait_for_results_to_load(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """Wait for LinkedIn search results to be present and visible."""
    print("[4/8] Waiting for LinkedIn search results container...")

    possible_result_locators = [
        (By.CSS_SELECTOR, "ul.reusable-search__entity-result-list"),
        (By.CSS_SELECTOR, "div.search-results-container"),
        (By.CSS_SELECTOR, "main.scaffold-layout__main"),
    ]

    loaded = False
    for locator in possible_result_locators:
        try:
            wait.until(EC.presence_of_element_located(locator))
            loaded = True
            break
        except TimeoutException:
            continue

    if not loaded:
        raise TimeoutException("Search results did not load within the timeout period.")

    print("[4/8] Waiting for profile links to appear...")
    wait.until(
        lambda d: len(
            d.find_elements(By.XPATH, "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]")
        )
        > 0
    )


def slow_scroll_results(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """Scroll gradually to trigger dynamic loading of additional profile cards."""
    print("[5/8] Performing gradual scrolling to load more results...")
    last_height = 0

    for i in range(1, SCROLL_ROUNDS_PER_PAGE + 1):
        try:
            current_height = driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
            step_target = int(current_height * (i / SCROLL_ROUNDS_PER_PAGE))

            driver.execute_script(
                "window.scrollTo({top: arguments[0], behavior: 'smooth'});",
                step_target,
            )

            previous_count = len(
                driver.find_elements(
                    By.XPATH,
                    "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]",
                )
            )

            wait.until(
                lambda d: (
                    len(
                        d.find_elements(
                            By.XPATH,
                            "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]",
                        )
                    )
                    >= previous_count
                )
            )

            new_height = driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
            print(f"    - Scroll round {i}/{SCROLL_ROUNDS_PER_PAGE} complete.")

            if i > 1 and new_height == last_height:
                print("    - Page height stabilized; continuing to extraction.")
                break

            last_height = new_height
        except TimeoutException:
            print(f"    - Scroll round {i} timed out while waiting for dynamic content; continuing.")
        except WebDriverException as err:
            print(f"    - Scroll round {i} encountered WebDriver issue: {err}")

    driver.execute_script(
        "window.scrollTo({top: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), behavior: 'smooth'});"
    )


def extract_profile_urls(driver: webdriver.Chrome) -> set:
    """Extract profile URLs from the current page and return as a set."""
    page_urls = set()
    print("[6/8] Extracting profile URLs from current page...")

    try:
        anchors = driver.find_elements(By.XPATH, "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]")
    except WebDriverException as err:
        print(f"    - Failed to collect anchor elements: {err}")
        return page_urls

    for anchor in anchors:
        try:
            href = anchor.get_attribute("href")
            if is_valid_linkedin_profile_url(href):
                page_urls.add(normalize_profile_url(href))
        except StaleElementReferenceException:
            continue
        except WebDriverException:
            continue

    print(f"    - Found {len(page_urls)} unique profile URL(s) on this page.")
    return page_urls


def click_next_page(driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
    """Click the next pagination button if available and enabled."""
    print("[7/8] Checking for next page button...")

    next_button_candidates = [
        (By.XPATH, "//button[contains(@aria-label,'Next')]"),
        (By.XPATH, "//button[contains(@class, 'artdeco-pagination__button--next')]"),
    ]

    next_button = None
    for locator in next_button_candidates:
        try:
            next_button = wait.until(EC.presence_of_element_located(locator))
            if next_button:
                break
        except TimeoutException:
            continue

    if not next_button:
        print("    - No next button found. Pagination finished.")
        return False

    is_disabled = (
        next_button.get_attribute("disabled") is not None
        or next_button.get_attribute("aria-disabled") == "true"
        or "disabled" in (next_button.get_attribute("class") or "").lower()
    )

    if is_disabled:
        print("    - Next button is disabled. Reached final page.")
        return False

    old_marker = len(driver.find_elements(By.XPATH, "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]"))

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        wait.until(EC.element_to_be_clickable((By.XPATH, "(//button[contains(@aria-label,'Next')])[1]")))
        next_button.click()
        print("    - Clicked next button.")
    except (TimeoutException, WebDriverException) as err:
        print(f"    - Failed to click next button: {err}")
        return False

    try:
        wait.until(
            lambda d: len(
                d.find_elements(By.XPATH, "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]")
            )
            != old_marker
        )
    except TimeoutException:
        print("    - Timed out waiting for next page content; continuing cautiously.")

    return True


def export_to_csv(urls: set) -> None:
    """Write deduplicated profile URLs into the target CSV file."""
    print("[8/8] Exporting URLs to CSV...")
    output_dir = os.path.dirname(OUTPUT_CSV_PATH)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    sorted_urls = sorted(urls)

    with open(OUTPUT_CSV_PATH, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["linkedin_profile_url"])
        for url in sorted_urls:
            writer.writerow([url])

    print(f"    - Export completed: {len(sorted_urls)} unique URL(s) saved.")
    print(f"    - File path: {OUTPUT_CSV_PATH}")


# -----------------------------
# Main Execution Flow
# -----------------------------
def main() -> None:
    driver = None
    all_profile_urls = set()

    try:
        driver = setup_driver()
        wait = WebDriverWait(driver, WAIT_SECONDS)

        print("Navigating to LinkedIn People search URL...")
        driver.get(SEARCH_URL)

        wait_for_results_to_load(driver, wait)

        page_number = 1
        while page_number <= MAX_PAGES:
            print(f"\n--- Processing page {page_number} ---")
            slow_scroll_results(driver, wait)
            page_urls = extract_profile_urls(driver)
            all_profile_urls.update(page_urls)
            print(f"Running total unique profile URLs: {len(all_profile_urls)}")

            if not click_next_page(driver, wait):
                break

            wait_for_results_to_load(driver, wait)
            page_number += 1

        export_to_csv(all_profile_urls)
        print("Scraping completed successfully.")

    except TimeoutException as err:
        print(f"ERROR: Timeout while waiting for LinkedIn elements: {err}")
        if all_profile_urls:
            export_to_csv(all_profile_urls)
    except NoSuchElementException as err:
        print(f"ERROR: Required page element was not found: {err}")
        if all_profile_urls:
            export_to_csv(all_profile_urls)
    except WebDriverException as err:
        print(f"ERROR: WebDriver encountered an issue: {err}")
        if all_profile_urls:
            export_to_csv(all_profile_urls)
    except Exception as err:
        print(f"ERROR: Unexpected failure occurred: {err}")
        if all_profile_urls:
            export_to_csv(all_profile_urls)
    finally:
        if driver is not None:
            print("Closing browser...")
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
