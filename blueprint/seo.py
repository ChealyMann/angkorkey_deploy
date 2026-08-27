from flask import Blueprint, Response, make_response, url_for, request
from datetime import datetime
from models import Product, Category, Brand

seo_bp = Blueprint("seo", __name__)

BASE_URL = "https://angkorkey.store"

@seo_bp.route("/robots.txt")
def robots_txt():
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /login
Disallow: /cart
Disallow: /api/
Disallow: /upload
Disallow: /loop

Sitemap: {BASE_URL}/sitemap.xml
"""
    response = make_response(content)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@seo_bp.route("/sitemap.xml")
def sitemap_xml():
    now_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Static pages
    pages = [
        {"loc": f"{BASE_URL}/", "changefreq": "daily", "priority": "1.0", "lastmod": now_str},
        {"loc": f"{BASE_URL}/products", "changefreq": "daily", "priority": "0.9", "lastmod": now_str},
        {"loc": f"{BASE_URL}/categories", "changefreq": "weekly", "priority": "0.8", "lastmod": now_str},
        {"loc": f"{BASE_URL}/brands", "changefreq": "weekly", "priority": "0.8", "lastmod": now_str},
        {"loc": f"{BASE_URL}/promotions", "changefreq": "daily", "priority": "0.8", "lastmod": now_str},
    ]

    # Dynamic Active Products
    try:
        products = (
            Product.query
            .join(Category, Product.category_id == Category.id)
            .filter(Product.status == "true", Category.status == "true")
            .all()
        )
        for p in products:
            pages.append({
                "loc": f"{BASE_URL}/product_detail/{p.id}",
                "changefreq": "weekly",
                "priority": "0.8",
                "lastmod": now_str
            })
    except Exception as e:
        print(f"Sitemap product generation error: {e}")

    # Dynamic Active Categories
    try:
        categories = Category.query.filter_by(status="true").all()
        for c in categories:
            pages.append({
                "loc": f"{BASE_URL}/category/{c.id}",
                "changefreq": "weekly",
                "priority": "0.7",
                "lastmod": now_str
            })
    except Exception as e:
        print(f"Sitemap category generation error: {e}")

    # Dynamic Active Brands
    try:
        brands = Brand.query.filter_by(status="true").all()
        for b in brands:
            pages.append({
                "loc": f"{BASE_URL}/brand/{b.id}",
                "changefreq": "weekly",
                "priority": "0.7",
                "lastmod": now_str
            })
    except Exception as e:
        print(f"Sitemap brand generation error: {e}")

    # Build XML
    xml_items = []
    for page in pages:
        xml_items.append(f"""    <url>
        <loc>{page['loc']}</loc>
        <lastmod>{page['lastmod']}</lastmod>
        <changefreq>{page['changefreq']}</changefreq>
        <priority>{page['priority']}</priority>
    </url>""")

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(xml_items)}
</urlset>"""

    response = make_response(xml_content)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=43200"
    return response
