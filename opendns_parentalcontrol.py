import os
import argparse
import logging
import sys
import datetime
import tempfile
import platform
import re
import configparser
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options  # Use FirefoxOptions if Firefox
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

# Add webdriver_manager for automatic driver downloads
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False
    logging.debug("webdriver_manager not installed; automatic driver download disabled")

# These settings will be overridden by command-line arguments if provided
HEADLESS = os.getenv('HEADLESS', 'true').lower() in ('true', 'yes', '1', 't')
LOG_FILE = os.getenv('LOG_FILE', 'opendns_filtering.log')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

# Map string log level to actual logging level
level_map = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}
log_level = level_map.get(LOG_LEVEL, logging.ERROR)

# Configure root logger
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
    ]
)

# Add file handler
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(log_level)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
logging.getLogger().addHandler(file_handler)

# Log startup information
logging.info(f"Starting OpenDNS Filtering script with log level: {LOG_LEVEL}")
logging.info(f"Log file: {os.path.abspath(LOG_FILE)}")
logging.debug(f"Python version: {sys.version}")
logging.debug(f"Platform: {platform.platform()}")
logging.debug(f"Working directory: {os.getcwd()}")

# Program Header
"""
OpenDNS Filtering Automation

Script to toggle OpenDNS content filtering categories via Selenium.
Configuration is loaded from opendns.conf (INI format) and environment variables override file settings.
Supports flags: --on, --off, -l/--list-categories, --login, --list-all-cat
Usage:
    python opendns_filtering.py [--on|--off] [-l|--list-categories] [--login] [--list-all-cat]
"""

# Load configuration from file and environment
CONFIG_PATH = os.getenv('OPENDNS_CONFIG', 'opendns.conf')
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

def get_config(param, env_var, fallback=None):
    return os.getenv(env_var) if os.getenv(env_var) is not None else config.get('opendns', param, fallback=fallback)

OPENDNS_USER = get_config('OPENDNS_USER', 'OPENDNS_USER')
OPENDNS_PASS = get_config('OPENDNS_PASS', 'OPENDNS_PASS')
NETWORK_ID = get_config('NETWORK_ID', 'NETWORK_ID')
# Default screenshot path uses the OS temp directory
DEFAULT_SCREENSHOT_PATH = os.getenv('SCREENSHOT_PATH') or os.path.join(tempfile.gettempdir(), 'opendns_error.png')
SCREENSHOT_PATH = get_config('SCREENSHOT_PATH', 'SCREENSHOT_PATH', DEFAULT_SCREENSHOT_PATH)
CATEGORIES = [c.strip() for c in get_config('CATEGORIES', 'CATEGORIES', 'Video Sharing, Social Networking').split(',')]
# Define a backup list of known categories in case dynamic fetching fails
ALL_CATEGORIES = [
    "Adult Themes", "Alcohol & Tobacco", "Anonymizers", "Arts & Entertainment",
    "Blogs", "Chat", "Chemicals", "Drugs", "Dynamic DNS", "Education",
    "Gambling", "Games", "Hacking", "Lingerie & Swimwear", "News & Media",
    "Phishing", "Proxies", "Sex Education", "Sexual & Erotica", "Shopping",
    "Social Networking", "Software & Malware", "Streaming Media", "Video Sharing",
    "Violence"
]

# This variable will be populated dynamically after login
DYNAMIC_CATEGORIES = []

def initialize_categories(driver=None):
    """Dynamically fetch categories from OpenDNS dashboard if possible,
    otherwise fall back to the predefined list.
    
    Args:
        driver: Optional WebDriver instance. If None, a new one will be created.
        
    Returns:
        List of category names
    """
    global DYNAMIC_CATEGORIES
    
    if DYNAMIC_CATEGORIES:
        # Return cached categories if already fetched
        return DYNAMIC_CATEGORIES
        
    if not driver:
        try:
            # Create a temporary driver if not provided
            temp_driver = get_driver()
            try:
                login_to_opendns(temp_driver, OPENDNS_USER, OPENDNS_PASS)
                ensure_custom_filtering(temp_driver, NETWORK_ID)
                DYNAMIC_CATEGORIES = get_available_categories(temp_driver)
                logging.info(f"Dynamically fetched {len(DYNAMIC_CATEGORIES)} categories")
                for i, category in enumerate(DYNAMIC_CATEGORIES, 1):
                    logging.info(f"  Category {i}: {category}")
                return DYNAMIC_CATEGORIES
            finally:
                temp_driver.quit()
        except Exception as e:
            logging.error(f"Failed to dynamically fetch categories: {e}")
            logging.warning("Falling back to predefined categories list")
            return ALL_CATEGORIES
    else:
        # Use the provided driver
        try:
            DYNAMIC_CATEGORIES = get_available_categories(driver)
            logging.info(f"Dynamically fetched {len(DYNAMIC_CATEGORIES)} categories")
            return DYNAMIC_CATEGORIES
        except Exception as e:
            logging.error(f"Failed to dynamically fetch categories with provided driver: {e}")
            logging.warning("Falling back to predefined categories list")
            return ALL_CATEGORIES

BROWSER = get_config('BROWSER', 'BROWSER', 'chrome').lower()
DEBUG_MODE = False  # Only save screenshots and page sources when --debug is set

# --- Helper Functions ---

def get_screenshots_dir():
    """Return the screenshots directory, creating it if needed."""
    screenshots_dir = os.path.join(os.getcwd(), 'screenshots')
    os.makedirs(screenshots_dir, exist_ok=True)
    return screenshots_dir

def timestamped_filename(prefix, ext=".png"):
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    return f"{ts}_{prefix}{ext}"

def save_screenshot(driver, stage, screenshots_dir=None):
    """Save a screenshot with a consistent naming pattern for better sorting."""
    if not DEBUG_MODE:
        return None
    if screenshots_dir is None:
        screenshots_dir = get_screenshots_dir()
    filename = timestamped_filename(stage)
    filepath = os.path.join(screenshots_dir, filename)
    driver.save_screenshot(filepath)
    logging.info(f"Screenshot saved: {filename}")
    return filepath

def save_page_source(driver, stage, screenshots_dir=None):
    if not DEBUG_MODE:
        return None
    if screenshots_dir is None:
        screenshots_dir = get_screenshots_dir()
    filename = timestamped_filename(stage, ext=".html")
    filepath = os.path.join(screenshots_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    logging.info(f"Page source saved: {filename}")
    return filepath

def robust_find_element(driver, by, value, timeout=10, screenshot_stage=None):
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        return element
    except Exception as e:
        # Log expected failures at DEBUG unless a screenshot_stage is provided
        if screenshot_stage:
            logging.error(f"Element not found: {by}={value} | {e}")
            save_screenshot(driver, screenshot_stage)
            save_page_source(driver, screenshot_stage)
        else:
            logging.debug(f"Element not found (quiet): {by}={value} | {e}")
        raise

def robust_click(element, driver=None, screenshot_stage=None):
    try:
        element.click()
    except Exception as e:
        logging.error(f"Error clicking element: {e}")
        if driver and screenshot_stage:
            save_screenshot(driver, screenshot_stage)
            save_page_source(driver, screenshot_stage)
        raise

# Add a generic helper to try multiple selectors
def try_selectors(driver, selectors, screenshot_stage=None):
    """
    Attempt each (By, value) in selectors, return the first matching WebElement.
    Logs at DEBUG for failures, INFO on success, and ERROR if none match.
    """
    for by, val in selectors:
        try:
            el = driver.find_element(by, val)
            logging.info(f"Found element using {by}: {val}")
            return el
        except Exception as e:
            logging.debug(f"Selector {by}:{val} failed: {e}")
    logging.error("None of the selectors matched any element")
    if screenshot_stage:
        save_screenshot(driver, screenshot_stage)
        save_page_source(driver, screenshot_stage)
    raise Exception("Element not found: tried multiple selectors")

def get_driver():
    # Support Chrome and Firefox
    if BROWSER == 'firefox':
        from selenium.webdriver.firefox.options import Options as FFOptions
        ff_opts = FFOptions()
        ff_opts.headless = HEADLESS
        # Optional Firefox binary override
        ff_bin = os.getenv('GECKO_BINARY')
        if ff_bin:
            ff_opts.binary_location = ff_bin
        # Initiate Firefox driver using Service
        gecko_path = os.getenv('GECKODRIVER_PATH')
        if gecko_path:
            service = Service(gecko_path)
            driver = webdriver.Firefox(service=service, options=ff_opts)
        else:
            driver = webdriver.Firefox(options=ff_opts)
        driver.implicitly_wait(10)
        return driver
    
    # Default to Chrome
    options = Options()
    if HEADLESS:
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")  # recommended on Windows
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    # Optional Chrome binary override
    chrome_bin = os.getenv('CHROME_BINARY')
    if chrome_bin:
        options.binary_location = chrome_bin
    
    try:
        chromedriver = os.getenv('CHROMEDRIVER_PATH')
        if chromedriver:
            # If a directory was provided, look for chromedriver.exe
            if os.path.isdir(chromedriver):
                possible_path = os.path.join(chromedriver, 'chromedriver.exe' if platform.system() == 'Windows' else 'chromedriver')
                if os.path.isfile(possible_path):
                    chromedriver = possible_path
                    logging.info(f"Found chromedriver in directory: {chromedriver}")
            
            if os.path.isfile(chromedriver):
                logging.info(f"Using chromedriver at: {chromedriver}")
                chrome_service = Service(chromedriver)
                driver = webdriver.Chrome(service=chrome_service, options=options)
            else:
                logging.warning(f"CHROMEDRIVER_PATH is not a valid file: {chromedriver}")
                driver = webdriver.Chrome(options=options)  # Fall back to auto-detection
        else:
            driver = webdriver.Chrome(options=options)
        driver.implicitly_wait(10)
        return driver
    except Exception as e:
        logging.error("Failed to initialize WebDriver for %s: %s", BROWSER, e)
        raise


def login_to_opendns(driver, user, password):
    """Login to OpenDNS dashboard with error handling"""
    driver.get("https://dashboard.opendns.com/signin")
    
    # Take screenshot before attempting login
    screenshots_dir = get_screenshots_dir()
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Use timestamp-first naming convention
    login_screenshot = os.path.join(screenshots_dir, f"{ts}_login_page.png")
    if DEBUG_MODE:
        driver.save_screenshot(login_screenshot)
    logging.info(f"Login page screenshot saved to: {login_screenshot}")
    
    # Debug what fields are visible
    page_source = driver.page_source
    logging.debug(f"Page title: {driver.title}")
    logging.debug(f"Current URL: {driver.current_url}")
    
    # Debug HTML structure - save with timestamp-first naming
    save_page_source(driver, ts)
    
    # Look for all input fields for debugging
    inputs = driver.find_elements(By.TAG_NAME, "input")
    logging.debug(f"Found {len(inputs)} input elements on page")
    for i, inp in enumerate(inputs):
        input_type = inp.get_attribute("type")
        input_name = inp.get_attribute("name")
        input_id = inp.get_attribute("id")
        logging.debug(f"Input #{i}: type={input_type}, name={input_name}, id={input_id}")
    
    # Look for all buttons
    buttons = driver.find_elements(By.TAG_NAME, "button")
    logging.debug(f"Found {len(buttons)} button elements on page")
    for i, btn in enumerate(buttons):
        btn_type = btn.get_attribute("type")
        btn_text = btn.text
        btn_id = btn.get_attribute("id")
        logging.debug(f"Button #{i}: type={btn_type}, id={btn_id}, text={btn_text}")
    
    try:
        # Try to find username field
        username_field = robust_find_element(driver, By.NAME, "username")
        username_field.send_keys(user)
        logging.debug("Found and filled username field")
        
        # Try to find password field
        password_field = robust_find_element(driver, By.NAME, "password")
        password_field.send_keys(password)
        logging.debug("Found and filled password field")
        
        # Try multiple selectors for the submit button
        submit_selectors = [
            (By.NAME, "submit"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[@type='submit']"),
            (By.XPATH, "//input[@type='submit']"),
            (By.XPATH, "//button[contains(text(), 'Sign')]"),
            (By.XPATH, "//button[contains(text(), 'Log')]")
        ]
        
        # Use generic helper to locate submit button
        submit_button = try_selectors(driver, submit_selectors, screenshot_stage="submit_button")
        robust_click(submit_button, driver, "submit_button")
        
        # Take screenshot after login attempt - timestamp first
        after_login_screenshot = os.path.join(screenshots_dir, f"{ts}_after_login.png")
        if DEBUG_MODE:
            driver.save_screenshot(after_login_screenshot)
        logging.info(f"After login screenshot saved to: {after_login_screenshot}")
        
    except Exception as e:
        logging.error(f"Login error: {e}")
        # Save error screenshot with timestamp first
        error_screenshot = os.path.join(screenshots_dir, f"{ts}_login_error.png")
        if DEBUG_MODE:
            driver.save_screenshot(error_screenshot)
        logging.info(f"Login error screenshot saved to: {error_screenshot}")
        raise


def ensure_custom_filtering(driver, network_id):
    """Navigate to content filtering and select 'custom' mode with more robust error handling"""
    url = f"https://dashboard.opendns.com/settings/{network_id}/content_filtering"
    driver.get(url)
    
    # Take screenshot to see the current page layout
    screenshots_dir = get_screenshots_dir()
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Use timestamp-first naming
    filtering_screenshot = os.path.join(screenshots_dir, f"{ts}_filtering_page.png")
    if DEBUG_MODE:
        driver.save_screenshot(filtering_screenshot)
    logging.info(f"Filtering page screenshot saved to: {filtering_screenshot}")
    
    # Log page source for debugging
    logging.debug(f"Current URL: {driver.current_url}")
    logging.debug(f"Page title: {driver.title}")
    
    # Try all possible selectors for the custom radio button with detailed logging
    try:
        # First try: check if we need to navigate to content filtering page via links
        if not "content_filtering" in driver.current_url:
            try:
                logging.info("Not on content filtering page, looking for content filtering link")
                content_link = driver.find_element(By.PARTIAL_LINK_TEXT, "Content Filtering")
                robust_click(content_link, driver, "content_filtering_link")
                # Take another screenshot after navigation with timestamp first
                after_nav_screenshot = os.path.join(screenshots_dir, f"{ts}_after_navigation.png")
                if DEBUG_MODE:
                    driver.save_screenshot(after_nav_screenshot)
                logging.info(f"After navigation screenshot saved to: {after_nav_screenshot}")
            except Exception as e:
                logging.warning(f"Could not find Content Filtering link: {e}")
        
        # Try multiple selectors for the custom radio button
        custom_selectors = [
            (By.XPATH, "//input[@type='radio' and @value='custom']"),
            (By.CSS_SELECTOR, "input[type='radio'][value='custom']"),
            (By.XPATH, "//input[@value='custom']"),
            (By.XPATH, "//input[contains(@id, 'custom')]"),
            (By.XPATH, "//label[contains(text(), 'Custom')]/input"),
            (By.XPATH, "//label[contains(text(), 'Custom')]")
        ]
        
        # Use generic helper to locate custom radio button
        custom_radio = try_selectors(driver, custom_selectors, screenshot_stage="filtering_custom_radio")
        # If label element, find associated input
        if custom_radio.tag_name.lower() == 'label':
            for_attr = custom_radio.get_attribute('for')
            if for_attr:
                try:
                    custom_radio = driver.find_element(By.ID, for_attr)
                    logging.info(f"Found radio via label's 'for': {for_attr}")
                except Exception:
                    logging.info("Clicking label since input not found")
        
        # Select if not already
        if not custom_radio.is_selected():
            robust_click(custom_radio, driver, "select_custom_radio")
            logging.info("Selected 'Custom' filtering mode")
        else:
            logging.info("'Custom' filtering mode already selected")
        
        # Save screenshot after
        save_screenshot(driver, "after_custom")
        return
        
    except Exception as e:
        logging.error(f"Error in ensure_custom_filtering: {e}")
        # Save error screenshot with timestamp first
        error_screenshot = os.path.join(screenshots_dir, f"{ts}_filtering_error.png")
        if DEBUG_MODE:
            driver.save_screenshot(error_screenshot)
        logging.info(f"Filtering error screenshot saved to: {error_screenshot}")
        raise


def get_available_categories(driver):
    """Fetch all category names directly from the OpenDNS web page
    
    This ensures we have the exact category names as they appear on the page,
    including any special characters or formatting.
    """
    logging.info("Scanning page for available categories...")
    categories = []
    
    try:
        # Create screenshots dir if it doesn't exist
        screenshots_dir = get_screenshots_dir()
        ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        
        # Take a screenshot before scanning
        before_scan_screenshot = os.path.join(screenshots_dir, f"{ts}_before_category_scan.png")
        if DEBUG_MODE:
            driver.save_screenshot(before_scan_screenshot)
        logging.info(f"Before category scan screenshot saved to: {before_scan_screenshot}")
        
        # Save current page HTML for analysis
        save_page_source(driver, ts)
        
        # Match the OpenDNS HTML structure exactly - looking for labels inside the categories div
        try:
            # First try to find all labels with for attribute matching dt_category[NUMBER] pattern
            category_labels = driver.find_elements(By.CSS_SELECTOR, "label[for^='dt_category[']")
            logging.info(f"Found {len(category_labels)} category labels with dt_category pattern")
            
            if not category_labels:
                # Alternative CSS selector if the first one doesn't work
                category_labels = driver.find_elements(By.XPATH, "//label[starts-with(@for, 'dt_category[')]")
                logging.info(f"Using alternative XPath selector, found {len(category_labels)} category labels")
            
            # Process each label
            for label in category_labels:
                try:
                    category_name = label.text.strip()
                    for_attr = label.get_attribute('for')
                    
                    if category_name and category_name.lower() not in ['low', 'medium', 'high', 'custom', 'none']:
                        if category_name not in categories:
                            logging.info(f"Found category: '{category_name}' (for={for_attr})")
                            categories.append(category_name)
                            
                            # Also try to find the checkbox to log its state (selected/not selected)
                            try:
                                checkbox = driver.find_element(By.ID, for_attr)
                                is_selected = checkbox.is_selected()
                                logging.debug(f"Category '{category_name}' checkbox state: {'selected' if is_selected else 'not selected'}")
                            except Exception:
                                logging.debug(f"Could not find checkbox for '{category_name}'")
                except Exception as e:
                    logging.debug(f"Error processing label: {e}")
                    continue
        except Exception as e:
            logging.warning(f"Error finding categories with primary method: {e}")
        
        # If we couldn't find categories, try more specific CSS selectors based on the full HTML structure
        if len(categories) < 1:
            logging.info("Primary method failed, trying more specific selectors...")
            
            # Try to find categories within the expected container structure
            try:
                # Look for the div with id="custom-setting" containing the categories
                custom_setting_div = driver.find_element(By.ID, "custom-setting")
                logging.info("Found custom-setting div container")
                
                # Find all category divs within the container
                category_divs = custom_setting_div.find_elements(By.CLASS_NAME, "category")
                logging.info(f"Found {len(category_divs)} category divs")
                
                for div in category_divs:
                    try:
                        # Find the label within this category div
                        label = div.find_element(By.TAG_NAME, "label")
                        category_name = label.text.strip()
                        for_attr = label.get_attribute('for')
                        
                        if category_name and category_name not in categories:
                            logging.info(f"Found category via div structure: '{category_name}' (for={for_attr})")
                            categories.append(category_name)
                            
                            # Check if this category is selected
                            try:
                                checkbox = div.find_element(By.TAG_NAME, "input")
                                is_selected = checkbox.is_selected()
                                logging.debug(f"Category '{category_name}' checkbox state: {'selected' if is_selected else 'not selected'}")
                            except Exception:
                                pass
                    except Exception as e:
                        logging.debug(f"Error processing category div: {e}")
                        continue
            except Exception as e:
                logging.warning(f"Error finding categories via div structure: {e}")
        
        # Fallback to checking just input elements if we still don't have categories
        if len(categories) < 1:
            logging.info("Trying fallback method with input elements...")
            
            try:
                # Find all input checkboxes with IDs starting with dt_category
                checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[id^='dt_category[']")
                logging.info(f"Found {len(checkboxes)} category checkboxes")
                
                for checkbox in checkboxes:
                    try:
                        checkbox_id = checkbox.get_attribute('id')
                        if not checkbox_id:
                            continue
                        
                        # Find the corresponding label
                        try:
                            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{checkbox_id}']")
                            category_name = label.text.strip()
                            
                            if category_name and category_name not in categories:
                                logging.info(f"Found category via checkbox: '{category_name}' (id={checkbox_id})")
                                categories.append(category_name)
                        except Exception:
                            logging.debug(f"Could not find label for checkbox {checkbox_id}")
                    except Exception as e:
                        logging.debug(f"Error processing checkbox: {e}")
                        continue
            except Exception as e:
                logging.warning(f"Error in fallback method: {e}")
        
        # If we couldn't find categories, fall back to predefined list but log a warning
        if not categories:
            logging.warning("Could not dynamically detect any categories, falling back to predefined list")
            return ALL_CATEGORIES
            
        logging.info(f"Successfully found {len(categories)} categories on the page")
        
        # Save the detected categories to a file for future reference
        with open(os.path.join(screenshots_dir, f"{ts}_detected_categories.txt"), "w", encoding="utf-8") as f:
            for cat in categories:
                f.write(f"{cat}\n")
        logging.info(f"Saved detected categories to: {ts}_detected_categories.txt")
        
        return categories
        
    except Exception as e:
        logging.error(f"Error in get_available_categories: {e}")
        # Capture more context about the page state
        screenshots_dir = get_screenshots_dir()
        ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        error_screenshot = os.path.join(screenshots_dir, f"{ts}_error_getting_categories.png")
        if DEBUG_MODE:
            driver.save_screenshot(error_screenshot)
        logging.error(f"Error screenshot saved to: {error_screenshot}")
        
        # Fall back to predefined list as a last resort
        logging.warning("Error detecting categories, falling back to predefined list")
        return ALL_CATEGORIES


def toggle_categories(driver, categories, block_list):
    """
    For each category on the page, check (block) if in block_list, uncheck (allow) if not in block_list.
    Categories not listed in block_list will be unchecked (allowed) by default.
    Args:
        driver: Selenium WebDriver
        categories: List of all categories detected on the page
        block_list: List of categories to block (from config)
    """
    for category in categories:
        try:
            label = robust_find_element(driver, By.XPATH, f"//label[contains(text(), '{category}')]")
            checkbox = driver.find_element(By.ID, label.get_attribute('for'))
            should_block = category in block_list
            if should_block and not checkbox.is_selected():
                robust_click(label, driver, f"block_{category.replace(' ', '_')}")
                logging.info("Blocked category: %s", category)
            elif not should_block and checkbox.is_selected():
                robust_click(label, driver, f"allow_{category.replace(' ', '_')}")
                logging.info("Allowed category: %s", category)
            else:
                logging.debug("No change for category: %s (should_block=%s, selected=%s)", category, should_block, checkbox.is_selected())
        except Exception as e:
            logging.error(f"Error toggling category {category}: {e}")
            continue  # Continue with next category instead of failing completely


def wait_for_confirmation(driver, timeout=20):
    """
    Wait for the OpenDNS confirmation message after applying settings.
    Tries multiple XPaths and logs the found message.
    Returns the confirmation element or None if not found.
    """
    xpaths = [
        "//div[@id='save-categories-message' and contains(text(), 'Settings saved')]",
        "//div[contains(text(), 'Your settings have been updated')]",
        "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'settings')]",
        "//div[contains(@class, 'message') and contains(text(), 'saved')]"
    ]
    for xpath in xpaths:
        try:
            confirmation = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            logging.info(f"Settings update confirmation found with XPath: {xpath} | Text: {confirmation.text.strip()}")
            return confirmation
        except Exception:
            continue
    logging.warning("Could not find OpenDNS confirmation message after applying settings.")
    return None


def apply_and_confirm(driver, screenshot_path=None):
    try:
        try:
            applytoall_checkbox = driver.find_element(By.ID, "save-categories-applytoall")
            if not applytoall_checkbox.is_selected():
                robust_click(applytoall_checkbox, driver, "applytoall_checkbox")
                logging.info("Checked 'apply to all' checkbox.")
            else:
                logging.info("'Apply to all' checkbox already checked.")
        except Exception as e:
            logging.warning(f"'Apply to all' checkbox not found or could not be checked: {e}")
        try:
            apply_button = driver.find_element(By.ID, "save-categories")
            robust_click(apply_button, driver, "apply_button")
            logging.info("Clicked 'Apply' button.")
        except Exception as e:
            logging.error(f"Could not find or click 'Apply' button: {e}")
            raise
        wait_for_confirmation(driver, timeout=20)
        if screenshot_path and DEBUG_MODE:
            driver.save_screenshot(screenshot_path)
            logging.info("Settings applied and screenshot saved to %s", screenshot_path)
        else:
            screenshots_dir = get_screenshots_dir()
            settings_applied_screenshot = os.path.join(screenshots_dir, timestamped_filename("settings_applied"))
            if DEBUG_MODE:
                driver.save_screenshot(settings_applied_screenshot)
            logging.info(f"Settings applied and screenshot saved to: {settings_applied_screenshot}")
        logging.info("Settings successfully applied and confirmed")
    except Exception as e:
        logging.error(f"Error applying settings: {e}")
        save_screenshot(driver, "error_applying_settings")
        save_page_source(driver, "error_applying_settings")
        raise


def read_category_status(driver, categories):
    """Return dict mapping category to blocked state (True if selected)"""
    status = {}
    try:
        if not categories:
            categories = get_available_categories(driver)
            logging.info(f"Using {len(categories)} categories detected from the page")
        for category in categories:
            try:
                label = robust_find_element(driver, By.XPATH, f"//label[contains(text(), '{category}')]")
                for_attr = label.get_attribute('for')
                if not for_attr:
                    logging.warning(f"Label for {category} does not have 'for' attribute")
                    continue
                checkbox = driver.find_element(By.ID, for_attr)
                is_selected = checkbox.is_selected()
                status[category] = is_selected
            except Exception as e:
                logging.error(f"Error processing category {category}: {e}")
                save_screenshot(driver, f"error_{category.replace(' ', '_')}")
                continue
        return status
    except Exception as e:
        logging.error(f"Error in read_category_status: {e}")
        save_screenshot(driver, "category_error")
        save_page_source(driver, "category_error")
        if status:
            logging.warning("Returning partial category status due to errors")
            return status
        raise


def print_category_status(status_map, header=None):
    if header:
        print(header)
    for cat, blocked in status_map.items():
        print(f"{cat}: {'Blocked' if blocked else 'Allowed'}")


def get_available_network_ids(driver):
    """Fetch all OpenDNS network IDs from settings page"""
    driver.get("https://dashboard.opendns.com/settings")
    links = WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, "//a[contains(@href, '/settings/') and contains(@href, 'content_filtering')]")
        )
    )
    ids = []
    for a in links:
        href = a.get_attribute('href')
        match = re.search(r"/settings/(\d+)/content_filtering", href)
        if match:
            nid = match.group(1)
            if nid not in ids:
                ids.append(nid)
    return ids


def save_current_configuration(status_map, username, password, network_id):
    """Save the current OpenDNS configuration to a file in the requested format
    Args:
        status_map: Dictionary mapping category names to their blocked status (True/False)
        username: OpenDNS username
        password: OpenDNS password
        network_id: OpenDNS network ID
    Returns:
        Path to the saved configuration file
    """
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"opendns.conf.{ts}"

    blocked_categories = [cat for cat, blocked in status_map.items() if blocked]
    allowed_categories = [cat for cat, blocked in status_map.items() if not blocked]
    all_categories = list(status_map.keys())

    # Use configparser to generate the config file for full compatibility
    config = configparser.ConfigParser()
    config.optionxform = str  # preserve case
    config['opendns'] = {
        'OPENDNS_USER': username,
        'OPENDNS_PASS': password,
        'NETWORK_ID': network_id,
        'SCREENSHOT_PATH': SCREENSHOT_PATH,
        'BLOCKED_CATEGORIES': ', '.join(blocked_categories),
        'ALLOWED_CATEGORIES': ', '.join(allowed_categories)
    }

    with open(filename, 'w', encoding='utf-8') as f:
        config.write(f)
        # Add extra sections as comments for human readability
        f.write(f"\n# [Summary]\n")
        for cat in all_categories:
            f.write(f"# {cat}: {'Blocked' if cat in blocked_categories else 'Allowed'}\n")
        f.write("\n# [All available categories]\n")
        for cat in all_categories:
            f.write(f"# {cat}\n")

    logging.info(f"Saved current configuration to: {filename}")
    return filename


def apply_configuration_from_file(filepath, driver):
    """Apply OpenDNS configuration from a file
    
    Args:
        filepath: Path to the configuration file
        driver: WebDriver instance
        
    Returns:
        Tuple of (username, password, network_id, categories_to_block)
    """
    logging.info(f"Loading configuration from: {filepath}")
    
    # Check if file exists
    if not os.path.isfile(filepath):
        logging.error(f"Configuration file not found: {filepath}")
        raise FileNotFoundError(f"Configuration file not found: {filepath}")
    
    # Read configuration file
    config = configparser.ConfigParser()
    config.read(filepath)
    
    # Extract configuration values
    try:
        username = config.get('opendns', 'OPENDNS_USER')
        password = config.get('opendns', 'OPENDNS_PASS')
        network_id = config.get('opendns', 'NETWORK_ID')
        
        # First try to get BLOCKED_CATEGORIES, fall back to CATEGORIES if not found
        if config.has_option('opendns', 'BLOCKED_CATEGORIES'):
            categories_to_block = [c.strip() for c in config.get('opendns', 'BLOCKED_CATEGORIES').split(',')]
            logging.info(f"Found {len(categories_to_block)} categories to block in configuration file")
        elif config.has_option('opendns', 'CATEGORIES'):
            categories_to_block = [c.strip() for c in config.get('opendns', 'CATEGORIES').split(',')]
            logging.info(f"Using CATEGORIES setting from configuration file")
        else:
            logging.error("No categories found in configuration file")
            raise ValueError("No categories found in configuration file")
            
        return username, password, network_id, categories_to_block
    except Exception as e:
        logging.error(f"Error parsing configuration file: {e}")
        raise


def main():
    # Declare globals at the start of the function
    global LOG_LEVEL, LOG_FILE, HEADLESS, DEBUG_MODE
    parser = argparse.ArgumentParser(description="Toggle OpenDNS categories or verify login")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true", help="Allow specified categories")
    group.add_argument("--off", action="store_true", help="Block specified categories")
    group.add_argument("-l", "--list-categories", action="store_true", help="List configured categories and exit")
    group.add_argument("--login", action="store_true", help="Verify OpenDNS authentication and exit")
    group.add_argument("--login-save-current", action="store_true", help="Login and save current configuration to a file")
    group.add_argument("--apply", metavar="CONFIG_FILE", help="Apply configuration from specified file")
    group.add_argument("--list-all-cat", action="store_true", help="List all supported categories and generate a sample config file")
    # Add logging and headless mode arguments
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], 
                       default=LOG_LEVEL, help="Set logging level")
    parser.add_argument("--log-file", default=LOG_FILE, help="Log file path")
    parser.add_argument("--headless", type=lambda x: x.lower() in ("true", "yes", "1", "t"), 
                       default=HEADLESS, help="Run browser in headless mode (true/false)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode: save screenshots and HTML copies")
    args = parser.parse_args()
    # Enable debug mode if requested
    DEBUG_MODE = args.debug

    # Configure logging based on command-line arguments
    LOG_LEVEL = args.log_level
    LOG_FILE = args.log_file
    HEADLESS = args.headless
    
    # Re-configure logging with updated parameters
    log_level = level_map.get(LOG_LEVEL, logging.INFO)
    # Clear existing handlers
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),  # Console output
        ]
    )
    # Add file handler
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
    logging.getLogger().addHandler(file_handler)
    
    # Log startup information with updated settings
    logging.info(f"Starting OpenDNS Filtering script with log level: {LOG_LEVEL}")
    logging.info(f"Log file: {os.path.abspath(LOG_FILE)}")
    logging.info(f"Headless mode: {HEADLESS}")
    logging.debug(f"Python version: {sys.version}")
    logging.debug(f"Platform: {platform.platform()}")
    logging.debug(f"Working directory: {os.getcwd()}")
      # If no flags provided, show help and usage examples
    if not any([args.on, args.off, args.list_categories, args.login, args.login_save_current, args.apply, args.list_all_cat]):
        parser.print_help()
        print("\nExamples:")
        print("  python opendns_filtering.py --off                    # Block configured categories")
        print("  python opendns_filtering.py --on                     # Allow configured categories")
        print("  python opendns_filtering.py -l                      # List configured categories")
        print("  python opendns_filtering.py --login                 # Verify login credentials")
        print("  python opendns_filtering.py --login-save-current    # Login and save current configuration")
        print("  python opendns_filtering.py --apply config.conf     # Apply configuration from file")
        print("  python opendns_filtering.py --list-all-cat          # Show all supported categories and make a sample config")
        sys.exit(0)

    if args.list_all_cat:
        for cat in ALL_CATEGORIES:
            print(cat)
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        fname = f"opendns.conf.sample.{ts}"
        sample = (
            "[opendns]\n"
            "OPENDNS_USER = \n"
            "OPENDNS_PASS = \n"
            "NETWORK_ID = \n"
            f"SCREENSHOT_PATH = {SCREENSHOT_PATH}\n"
            f"CATEGORIES = {', '.join(ALL_CATEGORIES)}\n"
        )
        with open(fname, 'w') as f:
            f.write(sample)
        print(f"Generated sample config: {fname}")
        sys.exit(0)

    if args.list_categories:
        for category in CATEGORIES:
            print(category)
        sys.exit(0)
        
    # Verify credentials only
    if args.login:
        driver = get_driver()
        try:
            login_to_opendns(driver, OPENDNS_USER, OPENDNS_PASS)
            # Auto-detect network_id if not set and only one network exists
            network_id = NETWORK_ID  # Create a local copy that we can modify
            if not network_id:
                available = get_available_network_ids(driver)
                if len(available) == 1:
                    network_id = available[0]
                    logging.info("Auto-detected network ID: %s", network_id)
                else:
                    parser.error("Multiple or no OpenDNS networks detected; please set NETWORK_ID in opendns.conf or env.")
            ensure_custom_filtering(driver, network_id)
            
            # Get categories directly from the web page
            web_categories = get_available_categories(driver)
            logging.info(f"Found {len(web_categories)} categories on web page")
            
            # print status upon login
            status = read_category_status(driver, web_categories)
            print("Current filter status after login:")
            print_category_status(status)
            sys.exit(0)
        except Exception as e:
            logging.error("Authentication failed: %s", e)
            print("Authentication failed")
            sys.exit(1)

    if args.login_save_current:
        driver = get_driver()
        try:
            login_to_opendns(driver, OPENDNS_USER, OPENDNS_PASS)
            network_id = NETWORK_ID
            if not network_id:
                available = get_available_network_ids(driver)
                if len(available) == 1:
                    network_id = available[0]
                    logging.info("Auto-detected network ID: %s", network_id)
                else:
                    parser.error("Multiple or no OpenDNS networks detected; please set NETWORK_ID in opendns.conf or env.")
            ensure_custom_filtering(driver, network_id)
            web_categories = get_available_categories(driver)
            status = read_category_status(driver, web_categories)
            print("Current filter status after login:")
            print_category_status(status)
            conf_path = save_current_configuration(status, OPENDNS_USER, OPENDNS_PASS, network_id)
            print(f"Configuration saved to: {conf_path}")
            sys.exit(0)
        except Exception as e:
            logging.error("Failed to save current configuration: %s", e)
            print("Failed to save current configuration")
            sys.exit(1)
        finally:
            driver.quit()

    # Apply configuration from file if requested
    if args.apply:
        driver = get_driver()
        try:
            username, password, network_id, categories_to_block = apply_configuration_from_file(args.apply, driver)
            login_to_opendns(driver, username, password)
            ensure_custom_filtering(driver, network_id)
            web_categories = get_available_categories(driver)
            # Only block categories that exist on the web page
            categories_to_block = [cat for cat in categories_to_block if cat in web_categories]
            pre_status = read_category_status(driver, web_categories)
            print("Status before applying configuration:")
            print_category_status(pre_status)
            toggle_categories(driver, web_categories, block_list=categories_to_block)
            apply_and_confirm(driver)
            post_status = read_category_status(driver, web_categories)
            print("Status after applying configuration:")
            print_category_status(post_status)
            print("Configuration applied successfully.")
            sys.exit(0)
        except Exception as e:
            logging.error("Failed to apply configuration: %s", e)
            print("Failed to apply configuration")
            sys.exit(1)
        finally:
            driver.quit()

    if not OPENDNS_USER or not OPENDNS_PASS:
        parser.error("Environment variables OPENDNS_USER and OPENDNS_PASS must be set.")
        
    categories = CATEGORIES
    driver = get_driver()
    
    try:
        login_to_opendns(driver, OPENDNS_USER, OPENDNS_PASS)
        ensure_custom_filtering(driver, NETWORK_ID)
        
        # Get updated categories from the webpage
        web_categories = get_available_categories(driver)
        logging.info(f"Found {len(web_categories)} categories on the web page")
        
        # If using predefined categories, check if they exist on the page
        if categories:
            # Check if user's categories exist on the page
            for cat in categories:
                if cat not in web_categories:
                    logging.warning(f"Category '{cat}' from configuration not found on web page")
        else:
            # If no categories specified, use all from web
            categories = web_categories
            logging.info("Using all categories found on the webpage")
            
        # status before applying changes
        pre_status = read_category_status(driver, categories)
        print("Status before changes:")
        print_category_status(pre_status)
        
        toggle_categories(driver, categories, block_list=CATEGORIES if args.off else [])
        
        # Use the standard screenshot function or custom path
        if SCREENSHOT_PATH == DEFAULT_SCREENSHOT_PATH:
            apply_and_confirm(driver)  # Uses timestamp-first naming internally
        else:
            apply_and_confirm(driver, SCREENSHOT_PATH)
            
        # status after applying changes
        post_status = read_category_status(driver, categories)
        print("Status after changes:")
        print_category_status(post_status)
        
        # Save the current configuration to a file
        save_current_configuration(post_status, OPENDNS_USER, OPENDNS_PASS, NETWORK_ID)
        
    except Exception:
        logging.exception("Automation error occurred")
        # Save error screenshot with timestamp
        screenshots_dir = get_screenshots_dir()
        ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        err_file = os.path.join(screenshots_dir, f"{ts}_automation_error.png")
        if DEBUG_MODE:
            driver.save_screenshot(err_file)
        logging.error("Saved error screenshot: %s", err_file)
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
