import csv
import os
import socket
import subprocess
import sys
import time
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
WAIT_SECONDS = 30
CHROME_STARTUP_TIMEOUT_SECONDS = 20


# -----------------------------
# URL Helpers
# -----------------------------
def is_valid_linkedin_profile_url(url: str) -> bool:
    """Return True only for valid LinkedIn profile URLs containing /in/."""
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

    return True


def normalize_profile_url(url: str) -> str:
    """Normalize LinkedIn profile URL to improve deduplication."""
    if not url:
        return ""

    cleaned = url.split("?", 1)[0].split("#", 1)[0].strip()
    return cleaned.rstrip("/")


# -----------------------------
# WebDriver Setup
# -----------------------------
def setup_driver() -> webdriver.Chrome:
    """Create a Chrome WebDriver attached to the requested existing Chrome profile."""
    print("[1/9] Preparing Chrome options for existing profile...")

    def build_profile_options() -> webdriver.ChromeOptions:
        profile_options = webdriver.ChromeOptions()
        profile_options.add_argument(f"--user-data-dir={CHROME_USER_DATA_DIR}")
        profile_options.add_argument(f"--profile-directory={CHROME_PROFILE_DIRECTORY}")
        profile_options.add_argument("--start-maximized")
        profile_options.add_argument("--disable-blink-features=AutomationControlled")
        profile_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        profile_options.page_load_strategy = "eager"
        return profile_options

    def find_windows_chrome_binary() -> str:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    def find_open_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temp_socket:
            temp_socket.bind(("127.0.0.1", 0))
            return int(temp_socket.getsockname()[1])

    def wait_for_debug_port(host: str, port: int, timeout_seconds: int) -> bool:
        end_time = time.time() + timeout_seconds
        while time.time() < end_time:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                time.sleep(0.5)
        return False

    print("[2/9] Downloading/locating compatible ChromeDriver (ChromeDriverManager)...")
    chromedriver_path = ChromeDriverManager().install()
    service = Service(chromedriver_path)

    print("[3/9] Launching Chrome WebDriver...")
    try:
        driver = webdriver.Chrome(service=service, options=build_profile_options())
        driver.set_page_load_timeout(90)
        print("[3/9] Chrome WebDriver launched successfully.")
        return driver
    except WebDriverException as first_error:
        message = str(first_error)
        if "session not created" not in message.lower():
            raise

        print("    - Direct profile attach failed. Retrying via remote debugging session...")
        chrome_binary = find_windows_chrome_binary()
        if not chrome_binary:
            raise WebDriverException(
                "Chrome binary was not found in default install paths. "
                "Install Chrome or update the chrome path in this script."
            ) from first_error

        debug_port = find_open_port()
        launch_command = [
            chrome_binary,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            f"--profile-directory={CHROME_PROFILE_DIRECTORY}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]

        try:
            subprocess.Popen(launch_command)
        except Exception as launch_error:
            raise WebDriverException(
                f"Could not launch Chrome for remote debugging: {launch_error}"
            ) from first_error

        if not wait_for_debug_port("127.0.0.1", debug_port, CHROME_STARTUP_TIMEOUT_SECONDS):
            raise WebDriverException(
                "Chrome remote debugging endpoint did not become available in time. "
                "Close existing Chrome windows using this profile and retry."
            ) from first_error

        attach_options = webdriver.ChromeOptions()
        attach_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
        attach_options.page_load_strategy = "eager"

        driver = webdriver.Chrome(service=service, options=attach_options)
        driver.set_page_load_timeout(90)
        print("[3/9] Chrome WebDriver attached successfully through remote debugging.")
        return driver


# -----------------------------
# LinkedIn Page Load Helpers
# -----------------------------
def wait_for_results_to_load(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """Wait until LinkedIn results UI and profile links are loaded."""
    print("[4/9] Waiting for LinkedIn search results page to load...")

    wait.until(lambda d: "linkedin.com" in d.current_url.lower())

    if "login" in driver.current_url.lower() or "checkpoint" in driver.current_url.lower():
        raise TimeoutException(
            "LinkedIn redirected to login/checkpoint. Please sign in with Profile 7 first and rerun."
        )

    container_locators = [
        (By.CSS_SELECTOR, "main.scaffold-layout__main"),
        (By.CSS_SELECTOR, "ul.reusable-search__entity-result-list"),
        (By.CSS_SELECTOR, "div.search-results-container"),
    ]

    container_loaded = False
    for locator in container_locators:
        try:
            wait.until(EC.presence_of_element_located(locator))
            container_loaded = True
            break
        except TimeoutException:
            continue

    if not container_loaded:
        raise TimeoutException("LinkedIn result container was not detected.")

    wait.until(
        lambda d: len(
            d.find_elements(By.XPATH, "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]")
        )
        > 0
    )

    print("[4/9] Search results are visible.")


# -----------------------------
# Scrolling and Extraction
# -----------------------------
def slow_scroll_results(driver: webdriver.Chrome) -> None:
    """Scroll progressively to trigger lazy loading of additional people cards."""
    print("[5/9] Scrolling page to load more results...")

    for round_index in range(1, SCROLL_ROUNDS_PER_PAGE + 1):
        try:
            previous_height = driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
            )
            previous_count = len(
                driver.find_elements(
                    By.XPATH,
                    "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]",
                )
            )

            target_position = int(previous_height * (round_index / SCROLL_ROUNDS_PER_PAGE))
            driver.execute_script(
                "window.scrollTo({top: arguments[0], behavior: 'smooth'});",
                target_position,
            )

            WebDriverWait(driver, 6).until(
                lambda d: (
                    len(
                        d.find_elements(
                            By.XPATH,
                            "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]",
                        )
                    )
                    > previous_count
                    or d.execute_script(
                        "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
                    )
                    > previous_height
                )
            )
            print(f"    - Scroll round {round_index}/{SCROLL_ROUNDS_PER_PAGE} loaded additional content.")
        except TimeoutException:
            print(f"    - Scroll round {round_index}/{SCROLL_ROUNDS_PER_PAGE} completed (no extra cards detected).")
        except WebDriverException as err:
            print(f"    - Scroll round {round_index}/{SCROLL_ROUNDS_PER_PAGE} WebDriver warning: {err}")

    driver.execute_script(
        "window.scrollTo({top: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), behavior: 'smooth'});"
    )


def extract_profile_urls(driver: webdriver.Chrome) -> set:
    """Extract only LinkedIn profile URLs from current page."""
    print("[6/9] Extracting profile URLs from current page...")
    urls = set()

    try:
        elements = driver.find_elements(
            By.XPATH,
            "//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')]",
        )
    except WebDriverException as err:
        print(f"    - Unable to fetch link elements: {err}")
        return urls

    for element in elements:
        try:
            href = element.get_attribute("href")
            if is_valid_linkedin_profile_url(href):
                urls.add(normalize_profile_url(href))
        except StaleElementReferenceException:
            continue
        except WebDriverException:
            continue

    print(f"    - Collected {len(urls)} unique profile URL(s) from this page.")
    return urls


# -----------------------------
# Pagination
# -----------------------------
def click_next_page(driver: webdriver.Chrome) -> bool:
    """Click next page button if available and enabled."""
    print("[7/9] Looking for next page button...")

    xpaths = [
        "//button[contains(@aria-label,'Next')]",
        "//button[contains(@class,'artdeco-pagination__button--next')]",
    ]

    next_button = None
    short_wait = WebDriverWait(driver, 8)

    for xpath in xpaths:
        try:
            candidate = short_wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            if candidate:
                next_button = candidate
                break
        except TimeoutException:
            continue

    if not next_button:
        print("    - Next button not found. Last page reached.")
        return False

    disabled = (
        next_button.get_attribute("disabled") is not None
        or next_button.get_attribute("aria-disabled") == "true"
        or "disabled" in (next_button.get_attribute("class") or "").lower()
    )

    if disabled:
        print("    - Next button is disabled. Pagination complete.")
        return False

    previous_first_link = ""
    try:
        first = driver.find_element(
            By.XPATH,
            "(//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')])[1]",
        )
        previous_first_link = first.get_attribute("href") or ""
    except NoSuchElementException:
        previous_first_link = ""

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, "(//button[contains(@aria-label,'Next')])[1]")))
        next_button.click()
        print("    - Next button clicked.")
    except (TimeoutException, WebDriverException) as err:
        print(f"    - Failed to click next button: {err}")
        return False

    try:
        WebDriverWait(driver, 15).until(
            lambda d: (
                d.current_url != SEARCH_URL
                or (
                    (d.find_element(By.XPATH, "(//a[contains(@href, '/in/') and contains(@href, 'linkedin.com')])[1]").get_attribute("href") or "")
                    != previous_first_link
                )
            )
        )
    except TimeoutException:
        print("    - Next page load wait timed out; continuing anyway.")

    return True


# -----------------------------
# CSV Output
# -----------------------------
def export_to_csv(urls: set) -> None:
    """Write deduplicated LinkedIn profile URLs to CSV."""
    print("[8/9] Writing results to CSV...")

    output_dir = os.path.dirname(OUTPUT_CSV_PATH)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    sorted_urls = sorted(urls)
    with open(OUTPUT_CSV_PATH, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["linkedin_profile_url"])
        for url in sorted_urls:
            writer.writerow([url])

    print(f"    - Saved {len(sorted_urls)} unique URL(s) to: {OUTPUT_CSV_PATH}")


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    """Run end-to-end LinkedIn people profile URL scraping flow."""
    driver = None
    all_urls = set()

    try:
        driver = setup_driver()
        wait = WebDriverWait(driver, WAIT_SECONDS)

        print("[9/9] Opening LinkedIn People search URL...")
        driver.get(SEARCH_URL)

        wait_for_results_to_load(driver, wait)

        page_number = 1
        while page_number <= MAX_PAGES:
            print(f"\n--- Scraping page {page_number} ---")

            slow_scroll_results(driver)
            page_urls = extract_profile_urls(driver)
            all_urls.update(page_urls)
            print(f"    - Running deduplicated total: {len(all_urls)}")

            if not click_next_page(driver):
                break

            wait_for_results_to_load(driver, wait)
            page_number += 1

        export_to_csv(all_urls)
        print("Scraping completed successfully.")
        return 0

    except TimeoutException as err:
        print(f"ERROR: Timeout while waiting for LinkedIn elements: {err}")
    except NoSuchElementException as err:
        print(f"ERROR: Missing expected page element: {err}")
    except WebDriverException as err:
        print(f"ERROR: WebDriver failure: {err}")
    except Exception as err:
        print(f"ERROR: Unexpected failure: {err}")
    finally:
        if all_urls:
            try:
                export_to_csv(all_urls)
            except Exception as export_err:
                print(f"WARNING: Could not export partial results: {export_err}")
        if driver is not None:
            print("Closing browser...")
            driver.quit()

    return 1


if __name__ == "__main__":
    sys.exit(main())
