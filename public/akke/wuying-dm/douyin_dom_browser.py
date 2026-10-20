"""Select the consumer site explicitly; creator.douyin.com is a separate surface."""
from urllib.parse import urlparse


def find_douyin_page(browser):
    return next((page for context in browser.contexts for page in context.pages
                 if urlparse(page.url or "").hostname in ("www.douyin.com", "douyin.com")), None)
