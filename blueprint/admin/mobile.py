import os
import re
from werkzeug.security import generate_password_hash
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from extensions import db
from models import Product, ProductImage, Category, Brand, User, Setting
from models.Product import getAllProduct
from models.ProductVariant import (
    VariantType,
    VariantOption,
    ProductOptionType,
    ProductVariant,
    ProductVariantOption,
    ProductVariantImage,
    StockStatus
)
from form.ProductForm import ProductForm, ProductFormEdit, ProductImageAdd
from form.CategoryForm import CategoryForm
from form.BrandForm import BrandForm, BrandFormEdit
from form.PromotionForm import PromotionForm, PromotionFormEdit
from form.UserForm import UserForm, UserFormEdit
from form.VoucherForm import VoucherForm
from upload_service import save_image
from Webp import save_picture

mobile_bp = Blueprint("mobile", __name__, url_prefix="/admin/mobile")

@mobile_bp.context_processor
def inject_bottom_nav_visibility():
    endpoint = request.endpoint or ""
    hide = False
    if endpoint.startswith("mobile.") and any(suffix in endpoint for suffix in ["_add", "_edit", "add_image", "variant_settings", "variant_add", "variant_edit", "bulk_best_seller"]):
        hide = True
    return dict(hide_bottom_nav=hide)

# ============================================================
# Helpers
# ============================================================
def make_slug(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value

def build_option_key(options):
    sorted_options = sorted(options, key=lambda option: option.variant_type.slug)
    return "|".join(f"{option.variant_type.slug}:{make_slug(option.value)}" for option in sorted_options)

def refresh_variant_option_key(variant):
    options = [selected.option for selected in variant.selected_options]
    variant.option_key = build_option_key(options)

def delete_image_files(filename):
    if not filename or filename == "none.jpg":
        return
    paths = [
        os.path.join(current_app.root_path, "static/images", filename),
        os.path.join(current_app.root_path, "static/images", "resized_" + filename),
        os.path.join(current_app.root_path, "static/images", "thumb_" + filename),
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                pass

# ============================================================
# Dashboard
# ============================================================
@mobile_bp.route("/")
@mobile_bp.route("/dashboard")
def dashboard():
    # Gather statistics
    total_products = Product.query.count()
    total_categories = Category.query.count()
    total_brands = Brand.query.count()
    total_users = User.query.count()
    
    return render_template(
        "backend/admin/mobile/dashboard.html",
        total_products=total_products,
        total_categories=total_categories,
        total_brands=total_brands,
        total_users=total_users
    )

# ============================================================
# Product Management
# ============================================================
@mobile_bp.route("/product")
def product_list():
    output = []
    _products = Product.query.order_by(Product.id.desc()).all()
    for _product in _products:
        output.append({
            "id": _product.id,
            "name": _product.name or "",
            "price": float(_product.price or 0),
            "cost": float(_product.cost or 0),
            "status": _product.status or "true",
            "category_name": _product.category_name.name if _product.category_name else "-",
            "brand_name": _product.brand.name if _product.brand else "-",
            "image": _product.image or "none.jpg",
            "best_selling": bool(_product.best_selling),
            "desc": _product.desc or "",
            "images": list(set(
                [pi.image for pi in _product.images if pi.image and pi.image != "none.jpg"] +
                [vi.image for var in _product.variants for vi in var.images if vi.image and vi.image != "none.jpg"]
            )),
        })
    tg_user = Setting.get_val("telegram_username", "Angkorkey_Store")
    tg_chat = Setting.get_val("telegram_chat_id", "")
    
    if tg_chat.startswith("@"):
        tg_chat_link = f"https://t.me/{tg_chat[1:]}"
    elif tg_chat.startswith("https://t.me/"):
        tg_chat_link = tg_chat
    else:
        tg_chat_link = f"https://t.me/{tg_chat}" if tg_chat else "https://t.me/Angkorkeyy"

    default_template = (
        "╭━━━ 🎯 <b>NEW PRODUCT</b> ━━━╮\n\n"
        "✨ <b>{name}</b>\n\n"
        "💰 <b>Price:</b> <code>{price}$</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "🛒 <b>Order Now</b>\n"
        "⚡️ <a href=\"https://t.me/angkorkeywebsite_bot/angkorkey\">Visit Website</a>\n\n"
        "💬 <b>Contact Admin</b>\n"
        "👉 <a href=\"http://t.me/{telegram_username}\">Click Here</a>\n\n"
        "╰━━━━━━━━━━━━━━━━━━━╯"
    )
    tg_template = Setting.get_val("telegram_caption_template", default_template)

    return render_template(
        "backend/admin/mobile/product/product.html",
        output=output,
        telegram_username=tg_user,
        telegram_chat_link=tg_chat_link,
        telegram_caption_template=tg_template
    )

@mobile_bp.route("/product/add", methods=["GET", "POST"])
def product_add():
    form = ProductForm()
    if form.validate_on_submit():
        unique_filename = "none.jpg"
        if form.image.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            unique_filename = save_image(form.image.data, upload_dir, allowed_exts)

        product = Product(
            image=str(unique_filename),
            name=form.name.data,
            desc=form.desc.data,
            price=float(form.price.data or 0),
            cost=float(form.cost.data or 0),
            old_price=float(form.old_price.data or 0),
            status=form.status.data,
            best_selling=form.best_selling.data,
            category_id=form.category.data.id if form.category.data else None,
            brand_id=form.brand.data.id if form.brand.data else None
        )
        db.session.add(product)
        db.session.commit()
        flash("Product added successfully!", "success")
        return redirect(url_for("mobile.product_list"))
    return render_template("backend/admin/mobile/product/add.html", form=form)

@mobile_bp.route("/product/edit/<int:product_id>", methods=["GET", "POST"])
def product_edit(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductFormEdit()
    
    if form.validate_on_submit():
        if form.image.data:
            delete_image_files(product.image)
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            product.image = save_image(form.image.data, upload_dir, allowed_exts)

        product.name = form.name.data.strip()
        product.desc = form.desc.data.strip()
        product.price = float(form.price.data or 0)
        product.cost = float(form.cost.data or 0)
        product.old_price = float(form.old_price.data or 0)
        product.status = form.status.data.strip()
        product.best_selling = form.best_selling.data
        product.category_id = form.category.data.id if form.category.data else None
        product.brand_id = form.brand.data.id if form.brand.data else None

        db.session.commit()
        flash("Product updated successfully!", "success")
        return redirect(url_for("mobile.product_list"))

    if not form.is_submitted():
        form.name.data = product.name
        form.desc.data = product.desc
        form.price.data = product.price
        form.cost.data = product.cost
        form.old_price.data = product.old_price
        form.status.data = product.status
        form.best_selling.data = product.best_selling
        form.category.data = product.category_name
        form.brand.data = product.brand

    return render_template("backend/admin/mobile/product/edit.html", form=form, product=product)

@mobile_bp.route("/product/delete/<int:product_id>", methods=["POST"])
def product_delete(product_id):
    product = Product.query.get_or_404(product_id)
    delete_image_files(product.image)
    if product.images:
        for img in product.images:
            delete_image_files(img.image)
            db.session.delete(img)
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted successfully!", "success")
    return redirect(url_for("mobile.product_list"))

@mobile_bp.route("/product/fast-image-upload/<int:product_id>", methods=["POST"])
def product_fast_image_upload(product_id):
    product = Product.query.get_or_404(product_id)
    if "image" not in request.files:
        return {"status": "error", "message": "No file uploaded"}, 400
    
    file = request.files["image"]
    if not file or file.filename == "":
        return {"status": "error", "message": "No selected file"}, 400

    allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed_exts:
        return {"status": "error", "message": f"Extension .{ext} not allowed"}, 400

    # Save new image and delete old files
    delete_image_files(product.image)
    upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
    unique_filename = save_image(file, upload_dir, allowed_exts)
    
    product.image = str(unique_filename)
    db.session.commit()
    
    return {
        "status": "success",
        "message": "Image updated successfully!",
        "filename": unique_filename
    }

@mobile_bp.route("/product/telegram/<int:product_id>", methods=["POST"])
def product_post_telegram(product_id):
    import os
    import json
    import urllib.request
    import urllib.parse
    import urllib.error
    
    def encode_multipart(fields, files):
        boundary = b'----WebKitFormBoundary7MA4YWxkTrZu0gW'
        lines = []
        for key, val in fields.items():
            lines.append(b'--' + boundary)
            lines.append(f'Content-Disposition: form-data; name="{key}"'.encode('utf-8'))
            lines.append(b'')
            lines.append(str(val).encode('utf-8'))
        for key, (filename, file_content) in files.items():
            lines.append(b'--' + boundary)
            lines.append(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"'.encode('utf-8'))
            lines.append(b'Content-Type: image/jpeg')
            lines.append(b'')
            lines.append(file_content)
        lines.append(b'--' + boundary + b'--')
        lines.append(b'')
        body = b'\r\n'.join(lines)
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary.decode("utf-8")}'}
        return headers, body

    try:
        product = Product.query.get_or_404(product_id)
        
        bot_token = (Setting.get_val("telegram_bot_token") or "").strip()
        chat_id = (Setting.get_val("telegram_chat_id") or "").strip()
        
        if not bot_token or not chat_id:
            return {
                "status": "error",
                "message": "Please configure Telegram Bot Token and Chat ID in Settings first."
            }, 400

        data = request.get_json() or {}
        caption = data.get("caption", "").strip()
        selected_images = data.get("selected_images", [])
        
        if not caption:
            caption = f"<b>{product.name}</b>\n\nPrice: ${product.price:.2f}"

        image_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
        
        # Check which of the selected images exist
        valid_images = []
        for img in selected_images:
            if img and img != "none.jpg":
                path = os.path.join(image_dir, img)
                if os.path.exists(path) and os.path.isfile(path):
                    valid_images.append(img)
                    
        # Send text only if no images selected/valid
        if not valid_images:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML"
            }
            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=encoded_data, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                tg_res = json.loads(response.read().decode("utf-8"))
        
        # Send single image if only one valid
        elif len(valid_images) == 1:
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            fields = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML"
            }
            img_path = os.path.join(image_dir, valid_images[0])
            with open(img_path, "rb") as img_file:
                file_content = img_file.read()
            files = {
                "photo": (valid_images[0], file_content)
            }
            headers, body = encode_multipart(fields, files)
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=15) as response:
                tg_res = json.loads(response.read().decode("utf-8"))
                
        # Send media group (album) if multiple images
        else:
            url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
            media_group = []
            files = {}
            
            for idx, img_name in enumerate(valid_images):
                attach_key = f"photo_{idx}"
                media_item = {
                    "type": "photo",
                    "media": f"attach://{attach_key}"
                }
                if idx == 0:
                    media_item["caption"] = caption
                    media_item["parse_mode"] = "HTML"
                media_group.append(media_item)
                
                img_path = os.path.join(image_dir, img_name)
                with open(img_path, "rb") as img_file:
                    file_content = img_file.read()
                files[attach_key] = (img_name, file_content)
                
            fields = {
                "chat_id": chat_id,
                "media": json.dumps(media_group)
            }
            headers, body = encode_multipart(fields, files)
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=30) as response:
                tg_res = json.loads(response.read().decode("utf-8"))
                
        if not tg_res.get("ok"):
            error_desc = tg_res.get("description", "Unknown Telegram Error")
            return {"status": "error", "message": f"Telegram API: {error_desc}"}, 400
            
        return {"status": "success", "message": "Product posted to Telegram channel successfully!"}
        
    except urllib.error.HTTPError as e:
        try:
            error_data = json.loads(e.read().decode("utf-8"))
            desc = error_data.get("description", "Unknown Telegram Error")
            return {"status": "error", "message": f"Telegram API: {desc}"}, 400
        except Exception:
            return {"status": "error", "message": f"HTTP Error {e.code}: {e.reason}"}, 400
    except Exception as e:
        return {"status": "error", "message": f"Connection Error: {str(e)}"}, 500

@mobile_bp.route("/product/bulk_status", methods=["POST"])
def bulk_status():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    status_val = data.get("status", "true")
    if not ids:
        return {"status": "error", "message": "No products selected"}, 400

    products = Product.query.filter(Product.id.in_(ids)).all()
    for product in products:
        product.status = status_val
    db.session.commit()
    return {"status": "success", "message": f"Updated status of {len(products)} products"}

@mobile_bp.route("/product/bulk_delete", methods=["POST"])
def bulk_delete():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return {"status": "error", "message": "No products selected"}, 400

    products = Product.query.filter(Product.id.in_(ids)).all()
    for product in products:
        delete_image_files(product.image)
        if product.images:
            for img in product.images:
                delete_image_files(img.image)
                db.session.delete(img)
        db.session.delete(product)
    db.session.commit()
    return {"status": "success", "message": f"Deleted {len(products)} products"}

# ============================================================
# Product Images
# ============================================================
@mobile_bp.route("/product/add_image/<int:product_id>", methods=["GET", "POST"])
def product_add_image(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductImageAdd()
    if form.validate_on_submit():
        if form.images.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            for file in form.images.data:
                if file.filename != '':
                    filename = save_image(file, upload_dir, allowed_exts)
                    pi = ProductImage(image=filename, product_id=product.id)
                    db.session.add(pi)
            db.session.commit()
            flash("Images uploaded successfully!", "success")
            return redirect(url_for("mobile.product_add_image", product_id=product.id))
    return render_template("backend/admin/mobile/product/add_images.html", form=form, product=product)

@mobile_bp.route("/product/image/delete/<int:image_id>", methods=["POST"])
def product_image_delete(image_id):
    pi = ProductImage.query.get_or_404(image_id)
    delete_image_files(pi.image)
    product_id = pi.product_id
    db.session.delete(pi)
    db.session.commit()
    flash("Image deleted successfully!", "success")
    return redirect(url_for("mobile.product_add_image", product_id=product_id))

# ============================================================
# Product Variants
# ============================================================
@mobile_bp.route("/product/variant/<int:product_id>", methods=["GET", "POST"])
def product_variant_add(product_id):
    product = Product.query.get_or_404(product_id)
    variant_types = VariantType.query.all()
    existing_variants = ProductVariant.query.filter_by(product_id=product.id).all()

    if request.method == "POST":
        sku = request.form.get("sku", "").strip() or None
        price = float(request.form.get("price") or product.price or 0)
        old_price = request.form.get("old_price")
        old_price = float(old_price) if old_price else None
        cost = request.form.get("cost")
        cost = float(cost) if cost else None
        stock = request.form.get("stock", "in_stock")
        status = request.form.get("status", "true")

        # Gather selected options
        selected_option_ids = []
        for vt in variant_types:
            opt_id = request.form.get(f"option_{vt.slug}")
            if opt_id:
                selected_option_ids.append(int(opt_id))

        if not selected_option_ids:
            flash("Please choose at least one option to create a variant.", "error")
            return redirect(url_for("mobile.product_variant_add", product_id=product_id))

        options = VariantOption.query.filter(VariantOption.id.in_(selected_option_ids)).all()
        option_key = build_option_key(options)

        # Check duplication
        existing = ProductVariant.query.filter_by(product_id=product.id, option_key=option_key).first()
        if existing:
            flash("A variant with this options combination already exists.", "error")
            return redirect(url_for("mobile.product_variant_add", product_id=product_id))

        variant = ProductVariant(
            product_id=product.id,
            sku=sku,
            price=price,
            old_price=old_price,
            cost=cost,
            stock=stock,
            status=status,
            option_key=option_key
        )
        db.session.add(variant)
        db.session.flush()

        for opt in options:
            pvo = ProductVariantOption(variant_id=variant.id, option_id=opt.id)
            db.session.add(pvo)

        # Upload images
        uploaded_files = request.files.getlist("images")
        if uploaded_files:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            for file in uploaded_files:
                if file.filename != '':
                    filename = save_image(file, upload_dir, allowed_exts)
                    pvi = ProductVariantImage(variant_id=variant.id, image=filename)
                    db.session.add(pvi)

        db.session.commit()
        flash("Variant added successfully!", "success")
        return redirect(url_for("mobile.product_variant_add", product_id=product.id))

    return render_template(
        "backend/admin/mobile/product/variant_add.html",
        product=product,
        variant_types=variant_types,
        existing_variants=existing_variants
    )

@mobile_bp.route("/product/variant/edit/<int:variant_id>", methods=["GET", "POST"])
def product_variant_edit(variant_id):
    variant = ProductVariant.query.get_or_404(variant_id)
    product = variant.product
    variant_types = VariantType.query.all()
    selected_option_map = {selected.option.variant_type.slug: selected.option.id for selected in variant.selected_options}

    if request.method == "POST":
        variant.sku = request.form.get("sku", "").strip() or None
        variant.price = float(request.form.get("price") or 0)
        old_price = request.form.get("old_price")
        variant.old_price = float(old_price) if old_price else None
        cost = request.form.get("cost")
        variant.cost = float(cost) if cost else None
        variant.stock = request.form.get("stock", "in_stock")
        variant.status = request.form.get("status", "true")

        # Options
        selected_option_ids = []
        for vt in variant_types:
            opt_id = request.form.get(f"option_{vt.slug}")
            if opt_id:
                selected_option_ids.append(int(opt_id))

        if not selected_option_ids:
            flash("Please choose at least one option.", "error")
            return redirect(url_for("mobile.product_variant_edit", variant_id=variant_id))

        options = VariantOption.query.filter(VariantOption.id.in_(selected_option_ids)).all()
        option_key = build_option_key(options)

        # Duplication check
        existing = ProductVariant.query.filter(
            ProductVariant.product_id == product.id,
            ProductVariant.option_key == option_key,
            ProductVariant.id != variant.id
        ).first()
        if existing:
            flash("Another variant with this option combination already exists.", "error")
            return redirect(url_for("mobile.product_variant_edit", variant_id=variant_id))

        variant.option_key = option_key

        # Re-save selected options
        ProductVariantOption.query.filter_by(variant_id=variant.id).delete()
        for opt in options:
            pvo = ProductVariantOption(variant_id=variant.id, option_id=opt.id)
            db.session.add(pvo)

        # Upload files
        uploaded_files = request.files.getlist("images")
        if uploaded_files:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            for file in uploaded_files:
                if file.filename != '':
                    filename = save_image(file, upload_dir, allowed_exts)
                    pvi = ProductVariantImage(variant_id=variant.id, image=filename)
                    db.session.add(pvi)

        db.session.commit()
        flash("Variant updated successfully!", "success")
        return redirect(url_for("mobile.product_variant_add", product_id=product.id))

    return render_template(
        "backend/admin/mobile/product/variant_edit.html",
        variant=variant,
        product=product,
        variant_types=variant_types,
        selected_option_map=selected_option_map
    )

@mobile_bp.route("/product/variant/delete/<int:variant_id>", methods=["POST"])
def product_variant_delete(variant_id):
    variant = ProductVariant.query.get_or_404(variant_id)
    product_id = variant.product_id
    if variant.images:
        for img in variant.images:
            delete_image_files(img.image)
            db.session.delete(img)
    ProductVariantOption.query.filter_by(variant_id=variant.id).delete()
    db.session.delete(variant)
    db.session.commit()
    flash("Variant deleted successfully!", "success")
    return redirect(url_for("mobile.product_variant_add", product_id=product_id))

@mobile_bp.route("/product/variant/image/delete/<int:image_id>", methods=["POST"])
def product_variant_image_delete(image_id):
    pvi = ProductVariantImage.query.get_or_404(image_id)
    delete_image_files(pvi.image)
    variant_id = pvi.variant_id
    db.session.delete(pvi)
    db.session.commit()
    flash("Variant image deleted successfully!", "success")
    return redirect(url_for("mobile.product_variant_edit", variant_id=variant_id))

# ============================================================
# Global Variant Settings
# ============================================================
@mobile_bp.route("/product/variant-settings", methods=["GET", "POST"])
def variant_settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_type":
            name = request.form.get("name", "").strip()
            slug = make_slug(name)
            if not name:
                flash("Type name cannot be empty.", "error")
            else:
                existing = VariantType.query.filter_by(slug=slug).first()
                if existing:
                    flash(f"Type '{name}' already exists.", "error")
                else:
                    vt = VariantType(name=name, slug=slug)
                    db.session.add(vt)
                    db.session.commit()
                    flash(f"Variant type '{name}' added.", "success")
        
        elif action == "add_option":
            type_id = int(request.form.get("type_id") or 0)
            value = request.form.get("value", "").strip()
            meta = request.form.get("meta", "").strip() or None
            if not value:
                flash("Option value cannot be empty.", "error")
            else:
                existing = VariantOption.query.filter_by(type_id=type_id, value=value).first()
                if existing:
                    flash(f"Option value '{value}' already exists.", "error")
                else:
                    vo = VariantOption(type_id=type_id, value=value, meta=meta)
                    db.session.add(vo)
                    db.session.commit()
                    flash(f"Option '{value}' added successfully.", "success")
        
        elif action == "delete_type":
            type_id = int(request.form.get("type_id") or 0)
            vt = VariantType.query.get(type_id)
            if vt:
                VariantOption.query.filter_by(type_id=vt.id).delete()
                db.session.delete(vt)
                db.session.commit()
                flash("Variant type deleted.", "success")
        
        elif action == "delete_option":
            opt_id = int(request.form.get("option_id") or 0)
            vo = VariantOption.query.get(opt_id)
            if vo:
                db.session.delete(vo)
                db.session.commit()
                flash("Option deleted.", "success")

        return redirect(url_for("mobile.variant_settings"))

    variant_types = VariantType.query.all()
    return render_template("backend/admin/mobile/product/variant_settings.html", variant_types=variant_types)

# ============================================================
# Bulk Best Sellers
# ============================================================
@mobile_bp.route("/product/bulk-best-seller", methods=["GET", "POST"])
def bulk_best_seller():
    if request.method == "POST":
        selected_ids = request.form.getlist("selected_ids")
        # Turn off all best sellers first
        Product.query.update({Product.best_selling: False})
        if selected_ids:
            Product.query.filter(Product.id.in_([int(i) for i in selected_ids])).update({Product.best_selling: True}, synchronize_session=False)
        db.session.commit()
        flash("Best selling statuses updated successfully!", "success")
        return redirect(url_for("mobile.bulk_best_seller"))

    products = Product.query.all()
    return render_template("backend/admin/mobile/product/bulk_best_seller.html", products=products)

# ============================================================
# Categories
# ============================================================
@mobile_bp.route("/category")
def category_list():
    categories = Category.query.all()
    return render_template("backend/admin/mobile/category/category.html", categories=categories)

@mobile_bp.route("/category/add", methods=["GET", "POST"])
def category_add():
    form = CategoryForm()
    if form.validate_on_submit():
        filename = "none.jpg"
        if form.image.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            filename = save_image(form.image.data, upload_dir, allowed_exts)
        
        category = Category(
            image=str(filename),
            name=form.name.data,
            desc=form.desc.data,
            status=form.status.data
        )
        db.session.add(category)
        db.session.commit()
        flash("Category added successfully!", "success")
        return redirect(url_for("mobile.category_list"))
    return render_template("backend/admin/mobile/category/add.html", form=form)

@mobile_bp.route("/category/edit/<int:category_id>", methods=["GET", "POST"])
def category_edit(category_id):
    category = Category.query.get_or_404(category_id)
    form = CategoryForm()
    if form.validate_on_submit():
        if form.image.data:
            delete_image_files(category.image)
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            category.image = save_image(form.image.data, upload_dir, allowed_exts)
        
        category.name = form.name.data.strip()
        category.desc = form.desc.data.strip()
        category.status = form.status.data.strip()
        db.session.commit()
        flash("Category updated successfully!", "success")
        return redirect(url_for("mobile.category_list"))

    if not form.is_submitted():
        form.name.data = category.name
        form.desc.data = category.desc
        form.status.data = category.status
    return render_template("backend/admin/mobile/category/edit.html", form=form, category=category)

@mobile_bp.route("/category/delete/<int:category_id>", methods=["POST"])
def category_delete(category_id):
    category = Category.query.get_or_404(category_id)
    delete_image_files(category.image)
    db.session.delete(category)
    db.session.commit()
    flash("Category deleted successfully!", "success")
    return redirect(url_for("mobile.category_list"))

# ============================================================
# Brands
# ============================================================
@mobile_bp.route("/brand")
def brand_list():
    brands = Brand.query.all()
    return render_template("backend/admin/mobile/brand/brand.html", brands=brands)

@mobile_bp.route("/brand/add", methods=["GET", "POST"])
def brand_add():
    form = BrandForm()
    if form.validate_on_submit():
        filename = "none.jpg"
        if form.image.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            filename = save_image(form.image.data, upload_dir, allowed_exts)
        
        brand = Brand(
            image=str(filename),
            name=form.name.data,
            desc=form.desc.data,
            status=form.status.data
        )
        db.session.add(brand)
        db.session.commit()
        flash("Brand added successfully!", "success")
        return redirect(url_for("mobile.brand_list"))
    return render_template("backend/admin/mobile/brand/add.html", form=form)

@mobile_bp.route("/brand/edit/<int:brand_id>", methods=["GET", "POST"])
def brand_edit(brand_id):
    brand = Brand.query.get_or_404(brand_id)
    form = BrandFormEdit()
    if form.validate_on_submit():
        if form.image.data:
            delete_image_files(brand.image)
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            brand.image = save_image(form.image.data, upload_dir, allowed_exts)
        
        brand.name = form.name.data.strip()
        brand.desc = form.desc.data.strip()
        brand.status = form.status.data.strip()
        db.session.commit()
        flash("Brand updated successfully!", "success")
        return redirect(url_for("mobile.brand_list"))

    if not form.is_submitted():
        form.name.data = brand.name
        form.desc.data = brand.desc
        form.status.data = brand.status
    return render_template("backend/admin/mobile/brand/edit.html", form=form, brand=brand)

@mobile_bp.route("/brand/delete/<int:brand_id>", methods=["POST"])
def brand_delete(brand_id):
    brand = Brand.query.get_or_404(brand_id)
    delete_image_files(brand.image)
    db.session.delete(brand)
    db.session.commit()
    flash("Brand deleted successfully!", "success")
    return redirect(url_for("mobile.brand_list"))

# ============================================================
# Users
# ============================================================
@mobile_bp.route("/user")
def user_list():
    users = User.query.all()
    return render_template("backend/admin/mobile/user/user.html", users=users)

@mobile_bp.route("/user/add", methods=["GET", "POST"])
def user_add():
    form = UserForm()
    if form.validate_on_submit():
        filename = "none.jpg"
        if form.image.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            filename = save_image(form.image.data, upload_dir, allowed_exts)

        user = User(
            image=str(filename),
            username=form.username.data,
            phone=form.phone.data,
            password=generate_password_hash(form.password.data)
        )
        db.session.add(user)
        db.session.commit()
        flash("User added successfully!", "success")
        return redirect(url_for("mobile.user_list"))
    return render_template("backend/admin/mobile/user/add.html", form=form)

@mobile_bp.route("/user/edit/<int:user_id>", methods=["GET", "POST"])
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    form = UserFormEdit()
    if form.validate_on_submit():
        if form.image.data:
            delete_image_files(user.image)
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            user.image = save_image(form.image.data, upload_dir, allowed_exts)

        user.username = form.username.data.strip()
        user.phone = form.phone.data.strip()
        if form.password.data:
            user.password = generate_password_hash(form.password.data)

        db.session.commit()
        flash("User updated successfully!", "success")
        return redirect(url_for("mobile.user_list"))

    if not form.is_submitted():
        form.username.data = user.username
        form.phone.data = user.phone
    return render_template("backend/admin/mobile/user/edit.html", form=form, user=user)

@mobile_bp.route("/user/delete/<int:user_id>", methods=["POST"])
def user_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == "chealy":
        flash("Cannot delete default admin account.", "error")
        return redirect(url_for("mobile.user_list"))
    delete_image_files(user.image)
    db.session.delete(user)
    db.session.commit()
    flash("User deleted successfully!", "success")
    return redirect(url_for("mobile.user_list"))

# ============================================================
# Banners/Promotions
# ============================================================
@mobile_bp.route("/promotion")
def promotion_list():
    from models.Promotion import Promotion
    promotions = Promotion.query.all()
    return render_template("backend/admin/mobile/promotion/promotion.html", promotions=promotions)

@mobile_bp.route("/promotion/add", methods=["GET", "POST"])
def promotion_add():
    from models.Promotion import Promotion
    form = PromotionForm()
    if form.validate_on_submit():
        filename = "none.jpg"
        if form.image.data:
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            filename = save_image(form.image.data, upload_dir, allowed_exts)

        promo = Promotion(
            image=filename,
            title=form.title.data,
            subtitle=form.subtitle.data,
            link=form.link.data,
            button_text=form.button_text.data,
            is_active=form.is_active.data
        )
        db.session.add(promo)
        db.session.commit()
        flash("Banner added successfully!", "success")
        return redirect(url_for("mobile.promotion_list"))
    return render_template("backend/admin/mobile/promotion/add.html", form=form)

@mobile_bp.route("/promotion/edit/<int:promotion_id>", methods=["GET", "POST"])
def promotion_edit(promotion_id):
    from models.Promotion import Promotion
    promo = Promotion.query.get_or_404(promotion_id)
    form = PromotionFormEdit()
    if form.validate_on_submit():
        if form.image.data:
            delete_image_files(promo.image)
            allowed_exts = current_app.config.get("ALLOWED_EXTENSIONS", {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'})
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")
            promo.image = save_image(form.image.data, upload_dir, allowed_exts)

        promo.title = form.title.data
        promo.subtitle = form.subtitle.data
        promo.link = form.link.data
        promo.button_text = form.button_text.data
        promo.is_active = form.is_active.data

        db.session.commit()
        flash("Banner updated successfully!", "success")
        return redirect(url_for("mobile.promotion_list"))

    if not form.is_submitted():
        form.title.data = promo.title
        form.subtitle.data = promo.subtitle
        form.link.data = promo.link
        form.button_text.data = promo.button_text
        form.is_active.data = promo.is_active
    return render_template("backend/admin/mobile/promotion/edit.html", form=form, promo=promo)

@mobile_bp.route("/promotion/delete/<int:promotion_id>", methods=["POST"])
def promotion_delete(promotion_id):
    from models.Promotion import Promotion
    promo = Promotion.query.get_or_404(promotion_id)
    delete_image_files(promo.image)
    db.session.delete(promo)
    db.session.commit()
    flash("Banner deleted successfully!", "success")
    return redirect(url_for("mobile.promotion_list"))

# ============================================================
# Vouchers
# ============================================================
@mobile_bp.route("/voucher")
def voucher_list():
    from models.Voucher import Voucher
    vouchers = Voucher.query.all()
    return render_template("backend/admin/mobile/voucher/voucher.html", vouchers=vouchers)

@mobile_bp.route("/voucher/add", methods=["GET", "POST"])
def voucher_add():
    from models.Voucher import Voucher
    form = VoucherForm()
    if form.validate_on_submit():
        voucher = Voucher(
            code=form.code.data.strip().upper(),
            min_spend=float(form.min_spend.data or 0.0),
            usage_limit=form.usage_limit.data,
            usage_count=form.usage_count.data or 0,
            status=form.status.data
        )
        db.session.add(voucher)
        db.session.commit()
        flash("Voucher created successfully!", "success")
        return redirect(url_for("mobile.voucher_list"))
    return render_template("backend/admin/mobile/voucher/add.html", form=form)

@mobile_bp.route("/voucher/edit/<int:voucher_id>", methods=["GET", "POST"])
def voucher_edit(voucher_id):
    from models.Voucher import Voucher
    voucher = Voucher.query.get_or_404(voucher_id)
    form = VoucherForm()
    if form.validate_on_submit():
        voucher.code = form.code.data.strip().upper()
        voucher.min_spend = float(form.min_spend.data or 0.0)
        voucher.usage_limit = form.usage_limit.data
        voucher.usage_count = form.usage_count.data or 0
        voucher.status = form.status.data
        db.session.commit()
        flash("Voucher updated successfully!", "success")
        return redirect(url_for("mobile.voucher_list"))

    if not form.is_submitted():
        form.code.data = voucher.code
        form.min_spend.data = voucher.min_spend
        form.usage_limit.data = voucher.usage_limit
        form.usage_count.data = voucher.usage_count
        form.status.data = voucher.status
    return render_template("backend/admin/mobile/voucher/edit.html", form=form, voucher=voucher)

@mobile_bp.route("/voucher/delete/<int:voucher_id>", methods=["POST"])
def voucher_delete(voucher_id):
    from models.Voucher import Voucher
    voucher = Voucher.query.get_or_404(voucher_id)
    db.session.delete(voucher)
    db.session.commit()
    flash("Voucher deleted successfully!", "success")
    return redirect(url_for("mobile.voucher_list"))

# ============================================================
# Settings
# ============================================================
@mobile_bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        telegram_username = request.form.get("telegram_username", "").strip()
        facebook_url = request.form.get("facebook_url", "").strip()
        tiktok_url = request.form.get("tiktok_url", "").strip()
        phone1 = request.form.get("phone1", "").strip()
        phone2 = request.form.get("phone2", "").strip()
        telegram_bot_token = request.form.get("telegram_bot_token", "").strip()
        telegram_chat_id = request.form.get("telegram_chat_id", "").strip()
        telegram_caption_template = request.form.get("telegram_caption_template", "").strip()

        if telegram_username.startswith("@"):
            telegram_username = telegram_username[1:]

        Setting.set_val("telegram_username", telegram_username)
        Setting.set_val("facebook_url", facebook_url)
        Setting.set_val("tiktok_url", tiktok_url)
        Setting.set_val("phone1", phone1)
        Setting.set_val("phone2", phone2)
        Setting.set_val("telegram_bot_token", telegram_bot_token)
        Setting.set_val("telegram_chat_id", telegram_chat_id)
        Setting.set_val("telegram_caption_template", telegram_caption_template)

        flash("Settings updated successfully.", "success")
        return redirect(url_for("mobile.settings"))

    default_template = (
        "╭━━━ 🎯 <b>NEW PRODUCT</b> ━━━╮\n\n"
        "✨ <b>{name}</b>\n\n"
        "💰 <b>Price:</b> <code>{price}$</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "🛒 <b>Order Now</b>\n"
        "⚡️ <a href=\"https://t.me/angkorkeywebsite_bot/angkorkey\">Visit Website</a>\n\n"
        "💬 <b>Contact Admin</b>\n"
        "👉 <a href=\"http://t.me/{telegram_username}\">Click Here</a>\n\n"
        "╰━━━━━━━━━━━━━━━━━━━╯"
    )

    return render_template(
        "backend/admin/mobile/settings.html",
        telegram_username=Setting.get_val("telegram_username", "Angkorkey_Store"),
        facebook_url=Setting.get_val("facebook_url", ""),
        tiktok_url=Setting.get_val("tiktok_url", ""),
        phone1=Setting.get_val("phone1", ""),
        phone2=Setting.get_val("phone2", ""),
        telegram_bot_token=Setting.get_val("telegram_bot_token", ""),
        telegram_chat_id=Setting.get_val("telegram_chat_id", ""),
        telegram_caption_template=Setting.get_val("telegram_caption_template", default_template),
    )
