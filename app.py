from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import base64, os, time, queue
from threading import Event
import json
from webdriver_manager.chrome import ChromeDriverManager

scraping_event = Event()
log_queue = queue.Queue()
driver = None
app = Flask(__name__)
SAVE_DIR = os.path.abspath("pdf_output")  # Default directory
os.makedirs(SAVE_DIR, exist_ok=True)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

def get_chrome_options(save_location):
    options = Options()
    options.add_argument("--headless=new")  # Updated headless mode syntax
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222")
    
    # Set download preferences
    prefs = {
        "download.default_directory": save_location,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    return options

@app.route('/scrape', methods=['POST'])
def scrape():
    global driver, SAVE_DIR
    scraping_event.clear()
    scraping_event.set()
    
    while not log_queue.empty():
        log_queue.get()

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400
            
        login_url = data.get("loginUrl")
        table_urls = data.get("urls", [])
        folder_name = data.get("folderName")
        start_index = data.get("startIndex")

        if not login_url or not table_urls or not folder_name:
            return jsonify({
                "error": "Missing required fields",
                "details": {
                    "loginUrl": "required" if not login_url else "provided",
                    "urls": "required" if not table_urls else "provided",
                    "folderName": "required" if not folder_name else "provided"
                }
            }), 400

        try:
            start_index = int(start_index) if start_index else 0
            if start_index < 1:
                start_index = 0
            else:
                start_index -= 1  # Convert to zero-based index
        except (TypeError, ValueError):
            start_index = 0

        # Create the user-specified folder inside pdf_output
        base_dir = os.path.abspath("pdf_output")
        save_dir = os.path.join(base_dir, folder_name)
        try:
            os.makedirs(save_dir, exist_ok=True)
            log_message(f"PDFs will be saved to: {save_dir}")
        except Exception as e:
            return jsonify({"error": f"Error creating save directory: {str(e)}"}), 400

        try:
            log_message("Initializing Chrome driver...")
            service = Service()
            driver = webdriver.Chrome(service=service, options=get_chrome_options(save_dir))
            wait = WebDriverWait(driver, 10)
            log_message("Chrome driver initialized successfully")

            driver.get(login_url)
            log_message("Opened login page")
            time.sleep(2)

            for table_idx, table_url in enumerate(table_urls):
                log_message(f"Opening table URL {table_idx + 1}")
                driver.execute_script(f"window.open('{table_url}', '_blank');")
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(3)

                try:
                    rows = wait.until(EC.presence_of_all_elements_located((By.XPATH, '//tr[starts-with(@id, "R")]')))
                    log_message(f"Found {len(rows)} rows in table {table_idx + 1}")
                except Exception as e:
                    log_message(f"Error finding rows in table {table_idx + 1}: {str(e)}")
                    continue

                for index in range(start_index, len(rows)):
                    try:
                        log_message(f"Processing row {index + 1}")
                        rows = driver.find_elements(By.XPATH, '//tr[starts-with(@id, "R")]')
                        if not rows:
                            log_message("No rows found, skipping...")
                            continue
                            
                        driver.execute_script("arguments[0].click();", rows[index])
                        time.sleep(3)

                        # Extract data from the table row
                        try:
                            row_data = rows[index].find_elements(By.TAG_NAME, "td")
                            waqf_id = row_data[0].text.strip() if len(row_data) > 0 else "unknown"
                            property_id = row_data[1].text.strip() if len(row_data) > 1 else "unknown"
                            district = row_data[2].text.strip() if len(row_data) > 2 else "unknown"
                            
                            filename = f"{waqf_id}_{property_id}_{district}.pdf"
                            filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))
                        except Exception as e:
                            log_message(f"Error extracting row data: {str(e)}")
                            filename = f"table{table_idx+1}_row{index+1}.pdf"

                        driver.switch_to.window(driver.window_handles[-1])
                        time.sleep(2)

                        try:
                            result = driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
                            if not result or 'data' not in result:
                                log_message("Failed to generate PDF, skipping...")
                                continue
                                
                            pdf_data = base64.b64decode(result['data'])
                            with open(os.path.join(save_dir, filename), "wb") as f:
                                f.write(pdf_data)
                            log_message(f"Saved: {filename}")
                        except Exception as e:
                            log_message(f"Error saving PDF: {str(e)}")
                            continue

                        driver.close()
                        driver.switch_to.window(driver.window_handles[-1])
                        time.sleep(1)

                    except Exception as e:
                        log_message(f"Error processing row {index + 1}: {str(e)}")
                        continue

                driver.close()
                driver.switch_to.window(driver.window_handles[0])

            return jsonify({
                "success": True,
                "message": f"Scraping completed. PDFs saved in 'pdf_output/{folder_name}' folder."
            })

        except Exception as e:
            log_message(f"Error during scraping: {str(e)}")
            return jsonify({
                "error": "Scraping failed",
                "details": str(e)
            }), 500

        finally:
            if driver:
                try:
                    driver.quit()
                    log_message("Browser closed")
                except Exception as e:
                    log_message(f"Error closing browser: {str(e)}")

    except Exception as e:
        log_message(f"Error processing request: {str(e)}")
        return jsonify({
            "error": "Request processing failed",
            "details": str(e)
        }), 500

def log_message(message):
    log_queue.put(message)
    print(message)  # Also print to console

@app.route('/stream')
def stream():
    def generate():
        while True:
            try:
                message = log_queue.get(timeout=1)
                yield f"data: {message}\n\n"
            except queue.Empty:
                if not scraping_event.is_set():
                    break
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/abort', methods=['POST'])
def abort_scraping():
    global driver
    scraping_event.clear()
    if driver:
        try:
            driver.quit()
            driver = None
            log_message("Browser closed due to abort request")
        except Exception as e:
            log_message(f"Error closing browser: {str(e)}")
    return jsonify({"message": "Operation aborted"}), 200

if __name__ == '__main__':
    app.run(debug=True)
