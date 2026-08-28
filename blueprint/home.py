from flask import Blueprint, render_template, request, jsonify, url_for, abort
from sqlalchemy.orm import subqueryload, joinedload, contains_eager
from sqlalchemy import func, or_, and_, case
from extensions import db, cache

from models import Category, Product, Promotion, Brand, Setting, Voucher, VoucherRedemption
from models.ProductVariant import (
    ProductVariant,
    ProductVariantOption,
    ProductVariantImage,
    ProductOptionType,
    VariantOption,
)

home_bp = Blueprint("home", __name__)


def image_url(filename):
    if not filename:
        return "https://images.unsplash.com/photo-1595225476474-87563907a212?q=80&w=800"

    return url_for("static", filename="images/" + filename)


@home_bp.route("/")
@home_bp.route("/home")
@cache.cached(timeout=120, key_prefix="view_home")
def home():
    products = (
        Product.query
        .join(Category, Product.category_id == Category.id)
        .filter(Product.status == "true", Category.status == "true")
        .order_by(Product.id.desc())
        .limit(10)
        .all()
    )
    promotions = Promotion.query.filter_by(is_active=True).all()

    return render_template(
        "frontend/pages/index.html",
        products=products,
        promotions=promotions
    )


@home_bp.route("/product_detail/<int:product_id>")
def product_detail(product_id):
    product = Product.query.options(
        subqueryload(Product.images),

        subqueryload(Product.variants)
        .subqueryload(ProductVariant.images),

        subqueryload(Product.variants)
        .subqueryload(ProductVariant.selected_options)
        .subqueryload(ProductVariantOption.option)
        .subqueryload(VariantOption.variant_type),

        subqueryload(Product.option_types)
        .subqueryload(ProductOptionType.variant_type),
    ).get_or_404(product_id)

    if product.status == "false" or (product.category_name and product.category_name.status == "false"):
        abort(404)

    # -------------------------------
    # Default product gallery
    # -------------------------------
    default_gallery_images = []

    if product.image:
        default_gallery_images.append({
            "src": image_url(product.image),
            "alt": product.name
        })

    for img in product.images:
        default_gallery_images.append({
            "src": image_url(img.image),
            "alt": product.name
        })

    if not default_gallery_images:
        default_gallery_images.append({
            "src": image_url(None),
            "alt": product.name
        })

    # -------------------------------
    # Active variants only
    # -------------------------------
    active_variants = [
        variant for variant in product.variants
        if variant.status == "true"
    ]

    # -------------------------------
    # Build option groups
    # Example:
    # Color: Black, White
    # Type: With Mic, No Mic
    # -------------------------------
    option_group_map = {}

    # First use product option types for correct order
    for product_option_type in sorted(
        product.option_types,
        key=lambda item: item.sort_order or 0
    ):
        variant_type = product_option_type.variant_type

        option_group_map[variant_type.slug] = {
            "name": variant_type.name,
            "slug": variant_type.slug,
            "sort_order": product_option_type.sort_order or 0,
            "options": [],
            "_seen": set()
        }

    # Then collect options actually used by variants
    for variant in active_variants:
        for selected in variant.selected_options:
            option = selected.option
            variant_type = option.variant_type

            if variant_type.slug not in option_group_map:
                option_group_map[variant_type.slug] = {
                    "name": variant_type.name,
                    "slug": variant_type.slug,
                    "sort_order": 999,
                    "options": [],
                    "_seen": set()
                }

            group = option_group_map[variant_type.slug]

            if option.id not in group["_seen"]:
                group["options"].append({
                    "id": option.id,
                    "value": option.value,
                    "meta": option.meta
                })
                group["_seen"].add(option.id)

    option_groups = []

    for group in option_group_map.values():
        group.pop("_seen", None)
        option_groups.append(group)

    option_groups = sorted(
        option_groups,
        key=lambda item: item["sort_order"]
    )

    # -------------------------------
    # Build variants JSON for frontend JS
    # -------------------------------
    variants_data = []

    for variant in active_variants:
        options = {}
        option_ids = {}

        for selected in variant.selected_options:
            option = selected.option
            variant_type = option.variant_type

            options[variant_type.slug] = option.value
            option_ids[variant_type.slug] = option.id

        variant_images = []

        sorted_images = sorted(
            variant.images,
            key=lambda item: item.sort_order or 0
        )

        for img in sorted_images:
            variant_images.append(image_url(img.image))

        # If variant has no own image, use product images
        if not variant_images:
            variant_images = [img["src"] for img in default_gallery_images]

        # --- SAFE ENUM EXTRACTOR FIX ---
        # Extracts the string value from the Enum so it is JSON serializable
        stock_val = variant.stock.value if hasattr(variant.stock, 'value') else variant.stock
        # -------------------------------

        variants_data.append({
            "id": variant.id,
            "sku": variant.sku,
            "price": variant.price,
            "old_price": variant.old_price,
            "cost": variant.cost,
            "stock": stock_val,  # <--- Now safely passes a JSON-compatible string
            "status": variant.status,
            "options": options,
            "option_ids": option_ids,
            "images": variant_images
        })

    # -------------------------------
    # Related products
    # -------------------------------
    related_products = (
        Product.query
        .join(Category, Product.category_id == Category.id)
        .filter(
            Product.category_id == product.category_id,
            Product.id != product.id,
            Product.status == "true",
            Category.status == "true"
        )
        .limit(4)
        .all()
    )

    if len(related_products) < 4:
        needed = 4 - len(related_products)
        excluded_ids = [p.id for p in related_products] + [product.id]

        more_products = (
            Product.query
            .join(Category, Product.category_id == Category.id)
            .filter(
                Product.id.notin_(excluded_ids),
                Product.status == "true",
                Category.status == "true"
            )
            .limit(needed)
            .all()
        )

        related_products.extend(more_products)

    return render_template(
        "frontend/pages/product-detail.html",
        product=product,
        related_products=related_products,
        option_groups=option_groups,
        variants_data=variants_data,
        default_gallery_images=default_gallery_images
    )

@home_bp.route("/cart")
def cart():
    return render_template("frontend/pages/cart.html")


@home_bp.route("/categories")
@cache.cached(timeout=300, key_prefix="view_all_categories")
def all_categories():
    categories = Category.query.filter_by(status="true").all()

    return render_template(
        "frontend/pages/all_categories.html",
        categories=categories
    )


@home_bp.route("/products")
@home_bp.route("/category/<int:category_id>")
def products(category_id=None):
    search_query = request.args.get("search", "").strip()
    if category_id is None:
        category_id = request.args.get("category_id", type=int)

    if category_id:
        Category.query.filter_by(id=category_id, status="true").first_or_404()

    is_ajax = request.args.get("ajax", type=int)

    # ------------------------------------------------------------
    # Get lowest active variant price for each product
    # If product has variants, sort by lowest variant price.
    # If product has no variants, sort by product.price.
    # ------------------------------------------------------------
    lowest_variant_price = (
        db.session.query(func.min(ProductVariant.price))
        .filter(
            ProductVariant.product_id == Product.id,
            ProductVariant.status == "true"
        )
        .correlate(Product)
        .scalar_subquery()
    )

    # Final sorting price
    sort_price = func.coalesce(lowest_variant_price, Product.price)

    query = (
        Product.query
        .join(Category, Product.category_id == Category.id)
        .filter(Product.status == "true", Category.status == "true")
        .options(
            joinedload(Product.brand),
            contains_eager(Product.category_name),
            subqueryload(Product.variants)
        )
    )

    if category_id:
        query = query.filter(Product.category_id == category_id)

    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    # ------------------------------------------------------------
    # Dynamic Sorting Logic
    # Brands are ordered alphabetically (A-Z, unbranded 'Other' last)
    # Products within each brand are ordered by user's chosen sort
    # ------------------------------------------------------------
    sort_by = request.args.get("sort", "featured").strip().lower()

    brand_order = [
        case((Brand.name.is_(None), 1), else_=0),
        Brand.name.asc()
    ]

    if sort_by == "price_asc":
        inner_order = [sort_price.asc(), Product.id.desc()]
    elif sort_by == "price_desc":
        inner_order = [sort_price.desc(), Product.id.desc()]
    elif sort_by == "newest":
        inner_order = [Product.id.desc()]
    else:  # 'featured' default
        inner_order = [
            Product.best_selling.desc(),
            sort_price.asc(),
            Product.id.desc()
        ]

    products_list = (
        query
        .outerjoin(Brand, Product.brand_id == Brand.id)
        .order_by(*(brand_order + inner_order))
        .all()
    )

    # ------------------------------------------------------------
    # Build brand groups for Jinja and JSON
    # ------------------------------------------------------------
    brand_groups_map = {}

    for product in products_list:
        brand_name = product.brand.name if product.brand else "Other"
        brand_id = product.brand.id if product.brand else 0
        brand_image = product.brand.image if (product.brand and product.brand.image and product.brand.image != "none.jpg") else None

        if brand_id not in brand_groups_map:
            brand_groups_map[brand_id] = {
                "brand_id": brand_id,
                "brand_name": brand_name,
                "brand_image": brand_image,
                "products": []
            }

        brand_groups_map[brand_id]["products"].append(product)

    brand_groups = list(brand_groups_map.values())

    if is_ajax:
        serialized_brand_groups = []
        for group in brand_groups:
            serialized_products = []
            for product in group["products"]:
                has_discount = bool(product.has_discount)
                discount_pct = 0
                price_val = product.promotion_min_price if getattr(product, 'discounted_variants', False) else product.price
                old_price_val = product.promotion_min_old_price if getattr(product, 'discounted_variants', False) else product.old_price
                if has_discount and old_price_val and float(old_price_val) > float(price_val):
                    discount_pct = int(round(((float(old_price_val) - float(price_val)) / float(old_price_val)) * 100))

                serialized_products.append({
                    "id": product.id,
                    "name": product.name,
                    "image": product.image,
                    "best_selling": bool(product.best_selling),
                    "has_discount": has_discount,
                    "discount_pct": discount_pct,
                    "price": float(product.price),
                    "price_text": product.price_text or f"${product.price:.2f}",
                    "promotion_old_price_text": product.promotion_old_price_text,
                    "promotion_price_text": product.promotion_price_text
                })

            serialized_brand_groups.append({
                "brand_id": group["brand_id"],
                "brand_name": group["brand_name"],
                "brand_image": group.get("brand_image"),
                "products": serialized_products
            })

        response = jsonify({
            "brand_groups": serialized_brand_groups,
            "product_count": len(products_list)
        })
        
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    categories = Category.query.filter_by(status="true").all()

    return render_template(
        "frontend/pages/products.html",
        products=products_list,
        brand_groups=brand_groups,
        categories=categories,
        current_category=category_id,
        sort_by=sort_by
    )


@home_bp.route("/promotions")
def promotions():
    search_query = request.args.get("search", "").strip()
    is_ajax = request.args.get("ajax", type=int)

    # ------------------------------------------------------------
    # Lowest discounted variant price
    # This is used for sorting promotion products.
    # It only checks active variants that really have discount.
    # ------------------------------------------------------------
    lowest_discount_variant_price = (
        db.session.query(func.min(ProductVariant.price))
        .filter(
            ProductVariant.product_id == Product.id,
            ProductVariant.status == "true",
            ProductVariant.old_price.isnot(None),
            ProductVariant.old_price > ProductVariant.price
        )
        .correlate(Product)
        .scalar_subquery()
    )

    # ------------------------------------------------------------
    # Sort price
    # If product has discounted variant, sort by lowest discounted variant price.
    # If product has normal product discount, sort by product.price.
    # ------------------------------------------------------------
    sort_price = func.coalesce(
        lowest_discount_variant_price,
        Product.price
    )

    # ------------------------------------------------------------
    # Promotion condition
    # Product will show on promotion page if:
    # 1. Product itself has discount
    # OR
    # 2. At least one active variant has discount
    # ------------------------------------------------------------
    promotion_condition = or_(
        and_(
            Product.old_price.isnot(None),
            Product.old_price > Product.price
        ),
        Product.variants.any(
            and_(
                ProductVariant.status == "true",
                ProductVariant.old_price.isnot(None),
                ProductVariant.old_price > ProductVariant.price
            )
        )
    )

    query = (
        Product.query
        .join(Category, Product.category_id == Category.id)
        .filter(
            Product.status == "true",
            Category.status == "true",
            promotion_condition
        )
        .options(
            joinedload(Product.brand),
            contains_eager(Product.category_name),
            subqueryload(Product.variants)
        )
    )

    # ------------------------------------------------------------
    # Search promotion products
    # ------------------------------------------------------------
    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    # ------------------------------------------------------------
    # Group by brand and sort low to high inside each brand
    # ------------------------------------------------------------
    query = query.outerjoin(Brand, Product.brand_id == Brand.id).order_by(
        Brand.name.asc().nullslast(),
        sort_price.asc(),
        Product.id.desc()
    )

    products = query.all()

    # ------------------------------------------------------------
    # Build brand groups for product_grid.html
    # product_grid.html already supports brand_groups.
    # ------------------------------------------------------------
    brand_groups_map = {}

    for product in products:
        brand_name = product.brand.name if product.brand else "No Brand"
        brand_id = product.brand.id if product.brand else 0

        if brand_id not in brand_groups_map:
            brand_groups_map[brand_id] = {
                "brand_id": brand_id,
                "brand_name": brand_name,
                "products": []
            }

        brand_groups_map[brand_id]["products"].append(product)

    brand_groups = list(brand_groups_map.values())

    # ------------------------------------------------------------
    # AJAX response for search
    # ------------------------------------------------------------
    if is_ajax:
        html = render_template(
            "frontend/layouts/product_grid.html",
            brand_groups=brand_groups,
            products=products
        )

        return jsonify({
            "html": html,
            "product_count": len(products)
        })

    # ------------------------------------------------------------
    # Normal page response
    # ------------------------------------------------------------
    return render_template(
        "frontend/pages/promotion_products.html",
        products=products,
        brand_groups=brand_groups
    )

@home_bp.route("/brands")
@cache.cached(timeout=300, key_prefix="view_all_brands")
def all_brands():
    brands = Brand.query.filter_by(status="true").order_by(Brand.name.asc()).all()

    return render_template(
        "frontend/pages/all_brands.html",
        brands=brands
    )


@home_bp.route("/brand/<int:brand_id>")
def brand_products(brand_id):
    brand = Brand.query.get_or_404(brand_id)
    if brand.status == "false":
        abort(404)

    page = request.args.get("page", 1, type=int)
    per_page = 10

    # ------------------------------------------------------------
    # Get lowest active variant price for each product
    # If product has variants, use lowest variant price.
    # If product has no variants, use normal product.price.
    # ------------------------------------------------------------
    lowest_variant_price = (
        db.session.query(func.min(ProductVariant.price))
        .filter(
            ProductVariant.product_id == Product.id,
            ProductVariant.status == "true"
        )
        .correlate(Product)
        .scalar_subquery()
    )

    sort_price = func.coalesce(lowest_variant_price, Product.price)

    pagination = (
        Product.query
        .join(Category, Product.category_id == Category.id)
        .filter(
            Product.brand_id == brand.id,
            Product.status == "true",
            Category.status == "true"
        )
        .options(
            joinedload(Product.brand),
            contains_eager(Product.category_name),
            subqueryload(Product.variants)
        )
        .order_by(
            sort_price.asc(),
            Product.id.desc()
        )
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )
    )

    products = pagination.items

    return render_template(
        "frontend/pages/brand_products.html",
        brand=brand,
        products=products,
        pagination=pagination
    )


def normalize_phone(phone):
    if not phone:
        return ""
    # Remove all non-digits except +
    clean = "".join(c for c in phone if c.isdigit() or c == '+')
    # If it starts with +855, replace with 0
    if clean.startswith("+855"):
        clean = "0" + clean[4:]
    # If it starts with 855, replace with 0
    elif clean.startswith("855"):
        clean = "0" + clean[3:]
    return clean


@home_bp.route("/api/validate_voucher", methods=["GET"])
def api_validate_voucher():
    code = request.args.get("code", "").strip().upper()
    subtotal = float(request.args.get("subtotal", 0))
    phone = request.args.get("phone", "").strip()
    
    if not code:
        return jsonify({"valid": False, "message": "Voucher code is required."}), 400
        
    voucher = Voucher.query.filter_by(code=code).first()
    
    if not voucher:
        return jsonify({"valid": False, "message": "Invalid voucher code."}), 200
        
    if voucher.status != "true":
        return jsonify({"valid": False, "message": "This voucher is inactive."}), 200
        
    if voucher.usage_limit is not None and voucher.usage_count >= voucher.usage_limit:
        return jsonify({"valid": False, "message": "Voucher usage limit reached."}), 200
        
    if subtotal < voucher.min_spend:
        return jsonify({
            "valid": False,
            "message": f"Minimum spend of ${voucher.min_spend:.2f} required to apply this voucher."
        }), 200
        
    # Check phone number validation if provided
    if phone:
        normalized = normalize_phone(phone)
        # Check if this phone number has already used this voucher
        redemption = VoucherRedemption.query.filter_by(
            voucher_id=voucher.id,
            phone_number=normalized
        ).first()
        if redemption:
            return jsonify({
                "valid": False,
                "message": "This phone number has already used this voucher code."
            }), 200
        
    return jsonify({
        "valid": True,
        "message": "Voucher applied successfully! Free standard delivery.",
        "code": voucher.code,
        "min_spend": voucher.min_spend
    }), 200


@home_bp.route("/api/use_voucher", methods=["POST"])
def api_use_voucher():
    data = request.get_json() or {}
    code = data.get("code", "").strip().upper()
    phone = data.get("phone", "").strip()
    
    if not code:
        return jsonify({"success": False, "message": "Voucher code is required."}), 400
    if not phone:
        return jsonify({"success": False, "message": "Phone number is required."}), 400
        
    voucher = Voucher.query.filter_by(code=code).first()
    
    if not voucher:
        return jsonify({"success": False, "message": "Invalid voucher code."}), 404
        
    if voucher.status != "true":
        return jsonify({"success": False, "message": "This voucher is inactive."}), 400
        
    if voucher.usage_limit is not None and voucher.usage_count >= voucher.usage_limit:
        return jsonify({"success": False, "message": "Voucher usage limit reached."}), 400
        
    normalized = normalize_phone(phone)
    
    # Check if this phone number has already used this voucher
    existing_redemption = VoucherRedemption.query.filter_by(
        voucher_id=voucher.id,
        phone_number=normalized
    ).first()
    if existing_redemption:
        return jsonify({"success": False, "message": "This phone number has already used this voucher code."}), 400
        
    try:
        # Create redemption log
        redemption = VoucherRedemption(
            voucher_id=voucher.id,
            phone_number=normalized
        )
        db.session.add(redemption)
        
        voucher.usage_count += 1
        db.session.commit()
        return jsonify({"success": True, "usage_count": voucher.usage_count}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500