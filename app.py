#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,re,sys,time,random,requests
from playwright.sync_api import sync_playwright

# --- 环境变量 ---
COOKIE_VALUE = os.environ.get('COOKIE_VALUE') or ""    # remember_web cookie 值，必填
EMAIL        = os.environ.get('EMAIL') or ""           # 登录邮箱,可选，作为备用,TG通知需要填写
PASSWORD     = os.environ.get('PASSWORD') or ""        # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token,可选
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID,可选
GH_TOKEN     = os.environ.get('GH_TOKEN') or ""        # GitHub PAT,用于自动回写 COOKIE_VALUE,可选

REMEMBER_COOKIE_NAME = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        # log(f"请求出口IP完成, status={resp.status_code}")
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    
    # 获取运行时间
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' 

    text = (
        f"🎉 HidenCloud 续期通知\n\n"
        f"{status}\n"
        f"👤 账号: {masked_email}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 续期时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

# --- 自动回写 cookie 到 GitHub Secret（解决"每次手动更新 cookie"）---
def update_github_secret(secret_name, new_value):
    if not GH_TOKEN:
        log("⚠️ 未配置 GH_TOKEN，无法自动更新 Secret")
        return False
    if not new_value or new_value == COOKIE_VALUE:
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    log(f"🔄 自动更新 Secret {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=60, check=False, env=env
        )
        if proc.returncode == 0:
            log("✅ Secret 更新成功")
            return True
        log(f"❌ Secret 更新失败: {proc.stderr.strip()[:200]}")
        return False
    except Exception as e:
        log(f"❌ Secret 更新异常: {e}")
        return False

def auto_update_cookie(context):
    """登录成功后，从浏览器提取最新 remember_web cookie 并回写 GitHub Secret"""
    try:
        cookies = context.cookies()
        new_value = None
        for c in cookies:
            if c.get("name") and c["name"].startswith("remember_web_"):
                new_value = c["value"]
                break
        if not new_value:
            log("ℹ️ 未找到 remember_web cookie，跳过自动回写")
            return
        if new_value == COOKIE_VALUE:
            log("✅ 当前 COOKIE_VALUE 已是最新，无需回写")
            return
        log("🔑 检测到新 cookie，尝试自动回写...")
        update_github_secret("COOKIE_VALUE", new_value)
    except Exception as e:
        log(f"⚠️ 自动回写 cookie 异常: {e}")

def handle_cloudflare(page, max_wait=90):
    """等待 Cloudflare 挑战通过（iframe 复选框 + 非 iframe JS 挑战 / Just a moment）"""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        url = page.url
        title = ""
        try:
            title = page.title().lower()
        except Exception:
            pass
        body_text = ""
        try:
            body_text = page.locator("body").inner_text().lower()
        except Exception:
            pass

        cf_active = ('__cf_chl' in url
                     or "just a moment" in title
                     or "security verification" in body_text
                     or page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0
                     or "we're verifying" in body_text)
        if not cf_active:
            return True

        log("⚠️ 检测到 Cloudflare 验证...")
        if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
            try:
                frame = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
                checkbox = frame.locator('input[type="checkbox"]')
                if checkbox.is_visible():
                    log("🖱️ 点击验证复选框...")
                    time.sleep(random.uniform(0.5, 1.5))
                    checkbox.click()
                    log("⏳ 已点击，等待验证结果...")
                    time.sleep(5)
            except Exception:
                pass
        # JS 挑战（Just a moment）：只需等待，浏览器会自动执行 JS
        time.sleep(2)
    log(f"❌ Cloudflare 验证超时（{max_wait}s）。当前 title: {page.title()[:60]}")
    return False

def login(page):
    # 1. Cookie 登录尝试
    if COOKIE_VALUE:
        log("📇 尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': REMEMBER_COOKIE_NAME,
                'value': COOKIE_VALUE,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
            cf_ok = handle_cloudflare(page)
            page_title = page.title()
            log(f"📝 当前Title: {page_title} | URL: {page.url[:100]}")
            if "auth/login" not in page.url:
                log(f"✅ Cookie 登录成功！当前已到达dashboard页面")
                return True
            log(f"❌ Cookie 失效，请更换（CF 通过={cf_ok}）")
        except Exception as e:
            log(f"❌ Cookie 登录异常: {e}")

    # 2. 账号密码登录
    if not EMAIL or not PASSWORD:
        return False
    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page.fill('input[name="email"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        time.sleep(0.5)
        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)
        handle_cloudflare(page)
        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page_title = page.title()
        log(f"📝 当前Title: {page_title}")
        if "auth/login" in page.url:
            log("❌ 登录失败。")
            return False
        log(f"✅ 账号密码登录成功！当前已到达dashboard页面")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        handle_cloudflare(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        # 方案1: 从 href 链接中提取 /service/数字/manage
        matches = re.findall(r'/service/(\d+)/manage', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从链接中获取到 Server ID: {server_id}")
            return server_id

        # 方案2: 从 span 标签中提取 #数字 (如 "Free Server #218079")
        matches = re.findall(r'#(\d{4,})', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
            return server_id

        log("❌ 所有 URL 均未找到 Server ID")
        return None
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        page.screenshot(path="server_id_error.png")
        return None

def get_due_date(page):
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"❌ 获取Due Date失败: {e}")
    return "未知"

def renew_service(page):

    try:
        log("➡ 进入续期流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice")')

        modal_opened = False
        for i in range(3):
            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                renew_btn.click()

                # 等待一小段时间，检测是否出现“未到续期时间”弹窗
                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"   # 特殊状态

                log("🖲️ 等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except:
                    log("⚠️ 弹窗未自动出现，尝试 JS 强制打开...")
                    try:
                        js_ret = page.evaluate("""() => {
                            const m = document.querySelector('[id^="renewService-"]');
                            if (!m) return 'no-modal';
                            m.classList.remove('hidden');
                            m.removeAttribute('aria-hidden');
                            m.style.display = 'block';
                            m.style.opacity = '1';
                            m.style.pointerEvents = 'auto';
                            return 'opened';
                        }""")
                        time.sleep(1)
                        create_btn.wait_for(state="visible", timeout=5000)
                        modal_opened = True
                        log(f"✅ JS 强制打开弹窗成功！({js_ret})")
                        break
                    except:
                        log("⚠️ JS 强制打开后仍未见 Create Invoice，准备重试...")
                        time.sleep(2)
            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            # dump modal DOM 结构
            try:
                modals = page.locator('[id^="renewService-"]').all()
                log(f"modal 元素数量: {len(modals)}")
                for i, m in enumerate(modals):
                    html = m.evaluate("el => el.outerHTML")
                    log(f"modal[{i}] outerHTML(前1500): {html[:1500]}")
            except Exception as e:
                log(f"modal dump 异常: {e}")
            # flowbite JS 是否加载
            try:
                has_flowbite = page.evaluate("typeof flowbite !== 'undefined'")
                has_modal_init = page.evaluate("typeof initModals !== 'undefined'")
                log(f"flowbite 全局: {has_flowbite} | initModals: {has_modal_init}")
            except Exception as e:
                log(f"flowbite 检查异常: {e}")
            # 尝试 JS 强制打开 modal
            try:
                opened = page.evaluate("() => { const m = document.querySelector('[id^=\"renewService-\"]'); if (!m) return 'no-modal'; m.classList.remove('hidden'); m.removeAttribute('hidden'); m.style.display='block'; m.setAttribute('aria-hidden','false'); return 'opened'; }")
                log(f"JS 强制打开 modal: {opened}")
                time.sleep(2)
                try:
                    body = page.locator("body").inner_text()
                    log(f"打开后页面文本(前700): {body[:700].replace(chr(10), ' | ')}")
                    cb = page.locator('button:has-text("Create Invoice")')
                    log(f"Create Invoice 按钮数量: {cb.count()}")
                    if cb.count() > 0:
                        cb_html = cb.first.evaluate("el => el.outerHTML")
                        log(f"Create Invoice outerHTML: {cb_html[:400]}")
                except Exception as e:
                    log(f"强制打开后检查异常: {e}")
                page.screenshot(path="renew_modal_forced.png", full_page=True)
            except Exception as e:
                log(f"JS 强制 modal 异常: {e}")
            page.screenshot(path="renew_modal_failed.png", full_page=True)
            return False

        handle_cloudflare(page)
        log("🖱️ 点击 'Create Invoice'...")
        create_btn.click()

        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
                log("⚠️ 遇到拦截，尝试处理...")
                handle_cloudflare(page)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
        handle_cloudflare(page)

        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.click()
        log("✅ 'Pay' 按钮已点击。")

        # 等待支付确认页面或跳转回服务页
        time.sleep(5)
        # 返回服务管理页面以获取新的到期时间
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        page.screenshot(path="renew_error.png")
        return False

def main():
    # 检查必要环境变量
    if not COOKIE_VALUE and not (EMAIL and PASSWORD):
        log("❌ 缺少登录凭证")
        sys.exit(1)

    global SERVICE_URL

    with sync_playwright() as p:
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")
            
            # 获取当前出口ip
            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            if not login(page):
                log("❌ 登录失败，尝试把浏览器里的最新 cookie 回写（如能拿到）")
                auto_update_cookie(context)
                sys.exit(1)

            # 登录成功后自动回写最新 cookie，避免每次手动更新
            auto_update_cookie(context)

            # 登录成功后，自动获取 Server ID
            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)
            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            # 获取旧到期时间
            old_due = get_due_date(page)
            log(f"📆 续费前到期时间：{old_due}")

            # 执行续费
            renew_result = renew_service(page)

            new_due = old_due
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:  # renew_result is True
                new_due = get_due_date(page)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            # 发送 Telegram 通知
            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except Exception as e:
            log(f"❌ 浏览器启动出错: {e}")
            sys.exit(1)
        finally:
            if 'browser' in locals() and browser:
                browser.close()
                
if __name__ == "__main__":
    main()
