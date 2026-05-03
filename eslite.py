import sqlite3
import time
import random
import os
import requests  # 用於下載圖片
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

class EsliteScraper:
    def __init__(self, db_name="eslite_books.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.setup_db()
        
        # 【新增：序號計數器】用於生成 book1, book2 等依序排列的邏輯序號 (跨頁累加)
        self.book_counter = 0
        
        # 【新增：建立資料夾】用於存放封面照片
        if not os.path.exists('covers'):
            os.makedirs('covers')
            
        options = webdriver.ChromeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        # 加上 User-Agent 偽裝，減少被封鎖機率
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36')
        self.options = options
        self.driver = webdriver.Chrome(options=self.options)
        self.wait = WebDriverWait(self.driver, 10)

    def setup_db(self):
        self.cursor.execute("DROP TABLE IF EXISTS Books")
        # 【修改：結構重組】Book_ID 為邏輯序號 (book1...)，ITEM_ID 為原始系統編碼 (ISBN)
        self.cursor.execute('''
            CREATE TABLE Books (
                Book_ID TEXT PRIMARY KEY,
                Book_URL TEXT,
                Book_Name TEXT,
                Author TEXT,
                Publisher TEXT,
                Price INTEGER,
                IssueDate TEXT,
                ImgURL TEXT,
                Description TEXT,
                ITEM_ID TEXT
            )
        ''')
        self.conn.commit()

    def close_ads(self):
        """處理滿版廣告，如果按鈕沒出來就發送 ESC 鍵"""
        print("  [檢查] 正在掃描廣告彈窗...")
        time.sleep(2) # 等廣告跑出來
        try:
            # 嘗試點擊紅色 X (通常有 close 或 p-close 字樣)
            close_btn = self.driver.find_elements(By.CSS_SELECTOR, "div[class*='close'], .p-close, .close-button")
            if close_btn and close_btn[0].is_displayed():
                close_btn[0].click()
                print("  [成功] 已關閉廣告點選。")
            else:
                # 暴力法：發送 ESC 鍵
                webdriver.ActionChains(self.driver).send_keys(webdriver.Keys.ESCAPE).perform()
                print("  [提示] 已嘗試發送 ESC 關閉廣告。")
        except:
            pass

    def handle_age_gate(self):
        try:
            btn = self.driver.find_elements(By.XPATH, "//button[contains(text(), '18') or contains(text(), '確定')]")
            if btn: btn[0].click()
        except:
            pass

    def download_image(self, url, filename):
        """ 下載照片並儲存為指定檔名 (如 book1.jpg) """
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                with open(f"covers/{filename}.jpg", "wb") as f:
                    f.write(response.content)
        except: pass

    def crawl_best_sellers(self, target_url):
        print(f"正在前往: {target_url}")
        self.url = target_url
        self.driver.get(target_url)
        time.sleep(3) 

        # 【條件：每頁 20 本, 爬取 5 頁】
        for page in range(1, 6):
            print(f"\n--- 正在爬取第 {page} 頁 ---")
            self.close_ads()
            self.handle_age_gate()
            
            # 確保滾動到位以觸發加載
            self.driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(2)

            main_window = self.driver.current_window_handle
            listing_url = self.driver.current_url

            # 抓取商品容器
            containers = self.driver.find_elements(By.CSS_SELECTOR, "div[class*='product-item'], .product-item")
            book_urls = []
            for box in containers:
                try:
                    links = box.find_elements(By.TAG_NAME, "a")
                    for l in links:
                        href = l.get_attribute("href")
                        text = l.text.strip()
                        if href and text and "/product/" in href:
                            book_urls.append((text, href))
                            break
                except: continue
            
            book_urls = book_urls[:20] # 抓取當前頁面的前 20 本
            print(f"  [成功] 第 {page} 頁找到 {len(book_urls)} 個項目，開始進入內頁...")

            for  i, (name, url) in enumerate(book_urls):
                # 【邏輯：擷取物理標籤】從 URL 擷取網址末端的長數字，作為 ITEM_ID
                item_id_val = url.rstrip('/').split('/')[-1]
                
                # 【邏輯：生成邏輯序號】每一筆資料都依序編號為 book1, book2...
                self.book_counter += 1
                logic_id = f"book{self.book_counter}"

                # 初始化預設值
                price, img_url, description = 0, "N/A", "暫無簡介"
                author, publisher, issue_date = "未知", "未知", "N/A"
                current_book_name = name  # 預設使用列表抓到的名稱
                is_book = False
                category = "讀取失敗"
                reason = "未進入商品頁"
                try:
                    self.driver.get(url)
                    time.sleep(random.uniform(2, 4))
                    self.handle_age_gate()
                    # ==========================================================
                    # ==========================================================
                    # 【最終優化版：自動抓取種類、名稱並列出狀態】
                    # ==========================================================
                    is_book = True
                    try:
                        # 1. 顯性等待：確保導航列載入 (最多等 8 秒)
                        nav_el = self.wait.until(EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "nav[aria-label='breadcrumb'], .ec-breadcrumb")
                        ))

                        # 2. 【核心改進】給麵包屑一點時間，並循環檢查直到抓到真正的分類
                        # 2. 【核心校正】循環等待，直到抓到「真正的分類」而不是「通用分類」
                        for _ in range(8):  # 最多等 4 秒
                            nav_els = self.driver.find_elements(By.CSS_SELECTOR, "nav[aria-label='breadcrumb'], .ec-breadcrumb")
                            if nav_els:
                                raw_text = nav_els[0].text.strip()
                                # 如果抓到的分類包含「書」或「動漫」，或是路徑長度夠長，就代表載入成功
                                if "書" in raw_text or "動漫" in raw_text or "雜誌" in raw_text or "分類" not in raw_text:
                                    path_parts = [p.strip() for p in raw_text.replace('\n', ' > ').split('>') if p.strip()]
                                    category = path_parts[1] if len(path_parts) >= 2 else "通用分類"
                                    if category != "通用分類":
                                        break
                            time.sleep(0.5)
                        # 4. 精準判定邏輯
                        exclude_cats = ["音樂", "專輯", "液態膠", "CD", "黑膠", "文具", "筆", "禮品", "生活百貨", "美容護理", "美妝保養", "服飾鞋包", "親子用品", "食品保健", "生活家居", "休閒戶外", "影音", "電子書", "雜誌"]
                        book_cats = ["中文書", "書", "動漫", "期刊", "Book"]
                        # --- A. 負向排除 ---
                        if any(c in category for c in exclude_cats):
                            is_book = False
                            reason = "類別排除"
                        # --- B. 正向收錄 ---
                        elif any(b in category for b in book_cats):
                            is_book = True
                        else:
                            is_book = False
                            reason = "非書籍類別"
                    except Exception as e:
                        category = "讀取失敗"
                        is_book = False
                        reason = f"發生錯誤 ({str(e)[:15]})"
                    # --- [精準修正：抓取價格] ---
                    try:
                        self.wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".item-price, .slider-price, .price-sale").text.strip() != "")
                        price_el = self.driver.find_element(By.CSS_SELECTOR, ".item-price, .slider-price, .price-sale")
                        price = int(''.join(filter(str.isdigit, price_el.text)))
                    except:
                        price = 0

                    # --- 執行跳過動作 (統一顯示格式) ---
                    if not is_book:
                        print(f"  [跳過] | 種類: {category} | 名稱: {name[:20]}... | ${price}| 原因: {reason}")
                        self.driver.get(listing_url)
                        time.sleep(2)
                        continue
                    # ------------------------------------------------------
                    # 【判定成功，執行資料抓取】
                    # ------------------------------------------------------
                    # (此處執行抓取作者、出版社、價格、簡介的代碼...)
                    # 假設價格變數為 price
                    
                    # --- 螢幕列出成功訊息 ---
                    print(f"  [成功] {logic_id} | 種類: {category} | 名稱: {name[:30]}... | ${price}")
                    # --- [精準修正：抓取作者與出版社] ---
                    # --- [精準修正：抓取作者與出版社] ---
                    try:
                        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[data-test-id='author-link'], a[data-test-id='supplier-link']")))
                        
                        author_el = self.driver.find_elements(By.CSS_SELECTOR, "a[data-test-id='author-link']")
                        if author_el: author = author_el[0].text.strip()
                        
                        pub_el = self.driver.find_elements(By.CSS_SELECTOR, "a[data-test-id='supplier-link']")
                        if pub_el: publisher = pub_el[0].text.strip()

                        # --- 【修正1：更精準抓取 IssueDate 排除發行商名稱】 ---
                        # --- 【修正後的 IssueDate 處理邏輯】 ---
                        try:
                            # 定位出版日期的 HTML 節點
                            date_elements = self.driver.find_elements(By.CSS_SELECTOR, "div.publicDate div.group span")
                            
                            if date_elements:
                                # 取得最後一個 span 的文字內容
                                raw_date = date_elements[-1].text.strip()
                                
                                # 判斷條件：若為空字串、或誤抓到包含「公司」的非日期資訊
                                if not raw_date or "公司" in raw_date:
                                    issue_date = "N/A"
                                else:
                                    # 正常日期 (例如 2026/04/10) 則保留
                                    issue_date = raw_date
                            else:
                                # 若完全找不到對應的 HTML 元素，設為 n/a
                                issue_date = "N/A"
                        except Exception:
                            # 發生任何非預期錯誤時，預設為 n/a
                            issue_date = "N/A"
                    except: pass

                    # --- 【修正2：精準抓取 Description 內容簡介】 ---
                    try:
                        # 定位到包含「內容簡介」文字的 div 區塊
                        desc_el = self.driver.find_element(By.CSS_SELECTOR, "div.product-description")
                        if desc_el:
                            description = desc_el.text.strip().replace('\n', ' ')
                            # 過濾掉 UI 上的「展開更多」字樣
                            description = description.replace("展開更多", "")
                    except: 
                        description = "暫無簡介"

                    # --- [抓取圖片與下載] ---
                    try:
                        img_el = self.driver.find_element(By.CSS_SELECTOR, "img[class*='inCoverImage1'], .product-image img")
                        img_url = img_el.get_attribute("src")
                        if img_url: self.download_image(img_url, logic_id)
                    except: pass
                    # --- 【補強：IssueDate 空值處理】 ---
                    if not issue_date or issue_date.strip() == "":
                        issue_date = "N/A"

                    # 4. 寫入資料庫
                    self.cursor.execute('''
                        INSERT OR REPLACE INTO Books VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (logic_id, url, name, author, publisher, price, issue_date, img_url, description, item_id_val))
                    self.conn.commit()
                    self.driver.get(listing_url)
                    time.sleep(2)
                except Exception as e:
                    # 【核心修改：救回在 try 區塊中崩潰的項目】
                    if not is_book:
                        # 若在進入判定前就崩潰 (如網頁超時)，現在會印出這行
                        print(f"  [跳過] | 種類: {category} | 書名: {current_book_name[:30]}... | ${price}| 原因: 異常({str(e)[:10]})")
                    self.driver.get(listing_url)
                    time.sleep(2)
                    continue

            # --- 【設計判斷到最後一頁的條件】 ---
            # 6. 翻頁按鈕
            if page < 5:
                try:
                    print(f"  [系統] 第 {page} 頁完成，檢查下一頁按鈕...")
                    if main_window not in self.driver.window_handles:
                        if self.driver.window_handles:
                            main_window = self.driver.window_handles[0]
                            self.driver.switch_to.window(main_window)
                    else:
                        self.driver.switch_to.window(main_window)
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                    
                    next_btn_selector = "div[data-gid='pagination-next'] button"
                    next_btns = self.driver.find_elements(By.CSS_SELECTOR, next_btn_selector)
                    
                    # 判斷條件：如果找不到按鈕，或按鈕狀態為 disabled (不具備可點擊屬性)，則視為末頁
                    if not next_btns or not next_btns[0].is_enabled():
                        print("  [提示] 找不到下一頁按鈕或已達末頁，停止爬取。")
                        break
                        
                    # 執行翻頁
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btns[0])
                    self.driver.execute_script("arguments[0].click();", next_btns[0])
                    time.sleep(5) # 等待頁面刷新
                except Exception as e:
                    print(f"  [提示] 翻頁失敗，結束迴圈: {str(e)[:30]}")
                    break
            
    def show_top_5_expensive(self):
        """ 【SELECT 取得價格最高前 5 本】 """
        print("\n" + "="*80)
        print(" 作業成果：全站價格最高的前 5 本書 ")
        print("="*80)
        # 使用 ORDER BY Price DESC (降冪排序) 並 LIMIT 5 (取前五名)
        self.cursor.execute('''
            SELECT Book_ID, Book_Name, Author, Publisher, Price 
            FROM Books 
            ORDER BY Price DESC 
            LIMIT 5
        ''')
        
        # 格式輸出：[書號] [書名] [作者] [出版社] [價格]
        for row in self.cursor.fetchall():
            print(f"Book ID: {row[0]}")
            print(f"Book Name: {row[1]}")
            print(f"Author: {row[2]}")
            print(f"Publisher: {row[3]}")
            print(f"Price: {row[4]}")
            print("-" * 40)

    def quit(self):
        self.conn.close()
        self.driver.quit()

if __name__ == "__main__":
    url = "https://www.eslite.com/best-sellers/online?type=2"
    scraper = EsliteScraper()
    try:
        scraper.crawl_best_sellers(url)
        scraper.show_top_5_expensive()
    except Exception as e:
        print(f"主程式發生錯誤: {e}")
    finally:
        scraper.quit()