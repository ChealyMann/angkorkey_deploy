import os
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app

from form.ProductForm import ProductForm, ProductFormEdit, ProductImageAdd
from models import Product, ProductImage, Category
from models.Product import getAllProduct
from extensions import db
from Webp import save_picture
from upload_service import save_image

from models.ProductVariant import (
    VariantType,
    VariantOption,
    ProductOptionType,
    ProductVariant,
    ProductVariantOption,
    ProductVariantImage,
    StockStatus
)

product_bp = Blueprint("product", __name__)


# ============================================================
# Helper functions
# ============================================================

def make_slug(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value


def normalize_option_value(value):
    return make_slug(value)


def build_option_key(options):
    sorted_options = sorted(
        options,
        key=lambda option: option.variant_type.slug
    )

    return "|".join(
        f"{option.variant_type.slug}:{normalize_option_value(option.value)}"
        for option in sorted_options
    )


def refresh_variant_option_key(variant):
    options = [selected.option for selected in variant.selected_options]
    variant.option_key = build_option_key(options)


def to_float_or_none(value):
    if value is None or str(value).strip() == "":
        return None

    return float(value)


def to_float_or_zero(value):
    if value is None or str(value).strip() == "":
        return 0

    return float(value)


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
                print(f"Error deleting file {path}: {e}")


def save_uploaded_file(file):
    if not file or not file.filename:
        return None

    return save_picture(file)


# ============================================================
# Product list
# ============================================================

@product_bp.route("/admin/product")
def product():
    output = []

    # keep this for pagination/footer if you still want it
    products = getAllProduct()

    # Vue search will use this full product list (ordered newest first)
    _products = Product.query.order_by(Product.id.desc()).all()

    for _product in _products:
        output.append({
            "id": _product.id,
            "name": _product.name or "",
            "desc": _product.desc or "",
            "price": float(_product.price or 0),
            "cost": float(_product.cost or 0),
            "status": _product.status or "true",
            "category_id": _product.category_id,
            "category_name": _product.category_name.name if _product.category_name else "-",
            "brand_id": _product.brand_id,
            "brand_name": _product.brand.name if _product.brand else "-",
            "image": _product.image or "none.jpg",
            "best_selling": bool(_product.best_selling),
        })

    return render_template(
        "backend/admin/pages/product/product.html",
        products=products,
        _products=_products,
        output=output,
        os=os
    )


# ============================================================
# Add product
# ============================================================

@product_bp.route("/admin/product/add", methods=["GET", "POST"])
def product_add():
    form = ProductForm()

    if form.validate_on_submit():
        unique_filename = "none.jpg"

        if form.image.data:
            # FIX: Use .get() and fallback sets to completely prevent KeyErrors
            allowed_exts = current_app.config.get(
                "ALLOWED_EXTENSIONS", 
                {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'}
            )
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")

            unique_filename = save_image(
                form.image.data,
                upload_dir,
                allowed_exts
            )

        _product = Product(
            name=form.name.data,
            desc=form.desc.data,
            price=form.price.data,
            old_price=form.old_price.data,
            image=str(unique_filename),
            cost=form.cost.data,
            status=form.status.data,
            category_id=form.category.data.id,
            brand_id=form.brand.data.id if form.brand.data else None,
            best_selling=form.best_selling.data,
        )

        try:
            db.session.add(_product)
            db.session.commit()
            flash("Product Added Successfully", "success")
        except Exception as e:
            db.session.rollback()
            print("Product Add Error:", e)
            flash("Database error occurred while adding product.", "error")

        return redirect(url_for("product.product"))

    return render_template(
        "backend/admin/pages/product/add.html",
        form=form
    )


# ============================================================
# Edit product
# ============================================================

@product_bp.route("/admin/product/edit/<int:product_id>", methods=["GET", "POST"])
def product_edit(product_id):
    _product = Product.query.get_or_404(product_id)
    form = ProductFormEdit()

    if form.validate_on_submit():
        _product.name = form.name.data
        _product.desc = form.desc.data
        _product.price = form.price.data
        _product.old_price = form.old_price.data
        _product.cost = form.cost.data
        _product.status = form.status.data
        _product.category_id = form.category.data.id
        _product.brand_id = form.brand.data.id if form.brand.data else None
        _product.best_selling = form.best_selling.data

        if form.image.data:
            delete_image_files(_product.image)

            # FIX: Use .get() and fallback sets to completely prevent KeyErrors
            allowed_exts = current_app.config.get(
                "ALLOWED_EXTENSIONS", 
                {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'}
            )
            upload_dir = current_app.config.get("UPLOAD_FOLDER", "static/images")

            _product.image = save_image(
                form.image.data,
                upload_dir,
                allowed_exts
            )

        try:
            db.session.commit()
            flash("Product Updated Successfully", "success")
        except Exception as e:
            db.session.rollback()
            print("Product Edit Error:", e)
            flash("Database error occurred while updating product.", "error")

        return redirect(url_for("product.product"))

    if request.method == "GET":
        form.name.data = _product.name
        form.desc.data = _product.desc
        form.price.data = _product.price
        form.old_price.data = _product.old_price
        form.cost.data = _product.cost
        form.status.data = _product.status
        form.category.data = _product.category_name
        form.brand.data = _product.brand if _product.brand else None
        form.best_selling.data = _product.best_selling

    return render_template(
        "backend/admin/pages/product/edit.html",
        form=form,
        product=_product,
        os=os
    )


# ============================================================
# Delete product
# ============================================================

@product_bp.route("/admin/product/delete/<int:product_id>", methods=["POST"])
def product_delete(product_id):
    _product = Product.query.get_or_404(product_id)

    try:
        delete_image_files(_product.image)

        for image in _product.images:
            delete_image_files(image.image)

        if hasattr(_product, "variants"):
            for variant in _product.variants:
                for img in variant.images:
                    delete_image_files(img.image)

        db.session.delete(_product)
        db.session.commit()

        flash("Product Deleted Successfully", "success")

    except Exception as e:
        db.session.rollback()
        print("Product Delete Error:", e)
        flash("Error deleting product.", "error")

    return redirect(url_for("product.product"))


@product_bp.route("/admin/product/bulk_delete", methods=["POST"])
def product_bulk_delete():
    from flask import jsonify
    data = request.get_json() or {}
    product_ids = data.get("ids", [])

    if not product_ids:
        return jsonify({"success": False, "message": "No products selected"}), 400

    try:
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        for _product in products:
            delete_image_files(_product.image)

            for image in _product.images:
                delete_image_files(image.image)

            if hasattr(_product, "variants"):
                for variant in _product.variants:
                    for img in variant.images:
                        delete_image_files(img.image)

            db.session.delete(_product)

        db.session.commit()
        flash(f"Successfully deleted {len(products)} products", "success")
        return jsonify({"success": True, "message": f"Successfully deleted {len(products)} products"})

    except Exception as e:
        db.session.rollback()
        print("Bulk Delete Error:", e)
        return jsonify({"success": False, "message": "Error deleting products"}), 500


@product_bp.route("/admin/product/bulk_status", methods=["POST"])
def product_bulk_status():
    from flask import jsonify
    data = request.get_json() or {}
    product_ids = data.get("ids", [])
    status = data.get("status")

    if not product_ids:
        return jsonify({"success": False, "message": "No products selected"}), 400

    if status not in ["true", "false"]:
        return jsonify({"success": False, "message": "Invalid status value"}), 400

    try:
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        for _product in products:
            _product.status = status

        db.session.commit()
        status_label = "Active" if status == "true" else "Inactive"
        flash(f"Successfully updated {len(products)} products to {status_label}", "success")
        return jsonify({"success": True, "message": f"Successfully updated {len(products)} products to {status_label}"})

    except Exception as e:
        db.session.rollback()
        print("Bulk Status Update Error:", e)
        return jsonify({"success": False, "message": "Error updating products status"}), 500


# ============================================================
# Product normal gallery images
# ============================================================

@product_bp.route("/admin/product/add_image/<int:product_id>", methods=["GET", "POST"])
def product_add_image(product_id):
    form = ProductImageAdd()
    product_images = Product.query.get_or_404(product_id)

    if form.validate_on_submit():
        if not form.images.data:
            flash("No images added", "error")
            return redirect(url_for("product.product_add_image", product_id=product_images.id))

        try:
            for file in form.images.data:
                if file and file.filename:
                    unique_filename = save_picture(file)

                    images_db = ProductImage(
                        product_id=product_id,
                        image=str(unique_filename)
                    )

                    db.session.add(images_db)

            db.session.commit()

            flash("Images Added Successfully", "success")
            return redirect(url_for("product.product_add_image", product_id=product_id))

        except Exception as e:
            db.session.rollback()
            print("Product Image Add Error:", e)
            flash("Error adding images.", "error")
            return redirect(url_for("product.product_add_image", product_id=product_id))

    return render_template(
        "backend/admin/pages/product/add_images.html",
        form=form,
        product=product_images
    )


@product_bp.route("/admin/product/image/delete/<int:image_id>", methods=["POST"])
def product_image_delete(image_id):
    image = ProductImage.query.get_or_404(image_id)
    product_id = image.product_id

    try:
        delete_image_files(image.image)

        db.session.delete(image)
        db.session.commit()

        flash("Image Deleted Successfully", "success")

    except Exception as e:
        db.session.rollback()
        print(f"Error deleting image record: {e}")
        flash("Error deleting image", "error")

    return redirect(url_for("product.product_add_image", product_id=product_id))


# ============================================================
# Variant settings page
# ============================================================

@product_bp.route("/admin/product/variant/settings")
def variant_settings():
    variant_types = VariantType.query.order_by(VariantType.id.asc()).all()

    variant_options = VariantOption.query.order_by(
        VariantOption.variant_type_id.asc(),
        VariantOption.id.asc()
    ).all()

    return render_template(
        "backend/admin/pages/product/variant_settings.html",
        variant_types=variant_types,
        variant_options=variant_options
    )


# ============================================================
# Variant Type CRUD
# ============================================================

@product_bp.route("/admin/product/variant/type/add", methods=["POST"])
def variant_type_add():
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip()

    if not name:
        flash("Variant type name is required.", "error")
        return redirect(url_for("product.variant_settings"))

    slug = make_slug(slug or name)

    if not slug:
        flash("Invalid variant type slug.", "error")
        return redirect(url_for("product.variant_settings"))

    if VariantType.query.filter_by(name=name).first():
        flash("Variant type name already exists.", "error")
        return redirect(url_for("product.variant_settings"))

    if VariantType.query.filter_by(slug=slug).first():
        flash("Variant type slug already exists.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        db.session.add(VariantType(name=name, slug=slug))
        db.session.commit()
        flash("Variant type added successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Type Add Error:", e)
        flash("Error adding variant type.", "error")

    return redirect(url_for("product.variant_settings"))


@product_bp.route("/admin/product/variant/type/edit/<int:type_id>", methods=["POST"])
def variant_type_edit(type_id):
    variant_type = VariantType.query.get_or_404(type_id)

    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip()

    if not name:
        flash("Variant type name is required.", "error")
        return redirect(url_for("product.variant_settings"))

    slug = make_slug(slug or name)

    name_exists = VariantType.query.filter(
        VariantType.name == name,
        VariantType.id != type_id
    ).first()

    if name_exists:
        flash("Another variant type already uses this name.", "error")
        return redirect(url_for("product.variant_settings"))

    slug_exists = VariantType.query.filter(
        VariantType.slug == slug,
        VariantType.id != type_id
    ).first()

    if slug_exists:
        flash("Another variant type already uses this slug.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        variant_type.name = name
        variant_type.slug = slug

        option_ids = [option.id for option in variant_type.options]

        if option_ids:
            links = ProductVariantOption.query.filter(
                ProductVariantOption.variant_option_id.in_(option_ids)
            ).all()

            touched_variant_ids = set()

            for link in links:
                if link.product_variant_id not in touched_variant_ids:
                    refresh_variant_option_key(link.variant)
                    touched_variant_ids.add(link.product_variant_id)

        db.session.commit()
        flash("Variant type updated successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Type Edit Error:", e)
        flash("Error updating variant type.", "error")

    return redirect(url_for("product.variant_settings"))


@product_bp.route("/admin/product/variant/type/delete/<int:type_id>", methods=["POST"])
def variant_type_delete(type_id):
    variant_type = VariantType.query.get_or_404(type_id)

    has_options = VariantOption.query.filter_by(variant_type_id=type_id).first()
    has_product_usage = ProductOptionType.query.filter_by(variant_type_id=type_id).first()

    if has_options or has_product_usage:
        flash("Cannot delete this type. Delete its options and product usage first.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        db.session.delete(variant_type)
        db.session.commit()
        flash("Variant type deleted successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Type Delete Error:", e)
        flash("Error deleting variant type.", "error")

    return redirect(url_for("product.variant_settings"))


# ============================================================
# Variant Option CRUD
# ============================================================

@product_bp.route("/admin/product/variant/option/add", methods=["POST"])
def variant_option_add():
    variant_type_id = request.form.get("variant_type_id", type=int)
    value = request.form.get("value", "").strip()
    meta = request.form.get("meta", "").strip() or None

    if not variant_type_id:
        flash("Please select variant type.", "error")
        return redirect(url_for("product.variant_settings"))

    if not value:
        flash("Option value is required.", "error")
        return redirect(url_for("product.variant_settings"))

    variant_type = VariantType.query.get_or_404(variant_type_id)

    duplicate = VariantOption.query.filter_by(
        variant_type_id=variant_type.id,
        value=value
    ).first()

    if duplicate:
        flash("This option already exists for this type.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        db.session.add(
            VariantOption(
                variant_type_id=variant_type.id,
                value=value,
                meta=meta
            )
        )

        db.session.commit()
        flash("Variant option added successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Option Add Error:", e)
        flash("Error adding variant option.", "error")

    return redirect(url_for("product.variant_settings"))


@product_bp.route("/admin/product/variant/option/edit/<int:option_id>", methods=["POST"])
def variant_option_edit(option_id):
    option = VariantOption.query.get_or_404(option_id)

    variant_type_id = request.form.get("variant_type_id", type=int)
    value = request.form.get("value", "").strip()
    meta = request.form.get("meta", "").strip() or None

    if not variant_type_id:
        flash("Please select variant type.", "error")
        return redirect(url_for("product.variant_settings"))

    if not value:
        flash("Option value is required.", "error")
        return redirect(url_for("product.variant_settings"))

    duplicate = VariantOption.query.filter(
        VariantOption.variant_type_id == variant_type_id,
        VariantOption.value == value,
        VariantOption.id != option_id
    ).first()

    if duplicate:
        flash("Another option already uses this value for this type.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        option.variant_type_id = variant_type_id
        option.value = value
        option.meta = meta

        links = ProductVariantOption.query.filter_by(
            variant_option_id=option.id
        ).all()

        for link in links:
            refresh_variant_option_key(link.variant)

        db.session.commit()
        flash("Variant option updated successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Option Edit Error:", e)
        flash("Error updating variant option.", "error")

    return redirect(url_for("product.variant_settings"))


@product_bp.route("/admin/product/variant/option/delete/<int:option_id>", methods=["POST"])
def variant_option_delete(option_id):
    option = VariantOption.query.get_or_404(option_id)

    used_by_variant = ProductVariantOption.query.filter_by(
        variant_option_id=option.id
    ).first()

    if used_by_variant:
        flash("Cannot delete this option because some variants are using it.", "error")
        return redirect(url_for("product.variant_settings"))

    try:
        db.session.delete(option)
        db.session.commit()
        flash("Variant option deleted successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Option Delete Error:", e)
        flash("Error deleting variant option.", "error")

    return redirect(url_for("product.variant_settings"))


# ============================================================
# Product Variant CRUD
# ============================================================

@product_bp.route("/admin/product/variant/<int:product_id>", methods=["GET", "POST"])
def product_variant_add(product_id):
    _product = Product.query.get_or_404(product_id)

    variant_types = VariantType.query.order_by(VariantType.id.asc()).all()

    existing_variants = ProductVariant.query.filter_by(
        product_id=product_id
    ).order_by(ProductVariant.id.desc()).all()

    if request.method == "POST":
        try:
            selected_option_ids = []

            for variant_type in variant_types:
                option_id = request.form.get(f"option_{variant_type.slug}")

                if option_id:
                    selected_option_ids.append(int(option_id))

            if not selected_option_ids:
                flash("Please select at least one variant option.", "error")
                return redirect(url_for("product.product_variant_add", product_id=product_id))

            selected_options = VariantOption.query.filter(
                VariantOption.id.in_(selected_option_ids)
            ).all()

            if len(selected_options) != len(selected_option_ids):
                flash("Some selected options are invalid.", "error")
                return redirect(url_for("product.product_variant_add", product_id=product_id))

            type_ids = [option.variant_type_id for option in selected_options]

            if len(type_ids) != len(set(type_ids)):
                flash("You can select only one option per type.", "error")
                return redirect(url_for("product.product_variant_add", product_id=product_id))

            sku = request.form.get("sku", "").strip()
            price = to_float_or_zero(request.form.get("price"))
            old_price = to_float_or_none(request.form.get("old_price"))
            cost = to_float_or_none(request.form.get("cost"))
            status = request.form.get("status", "true")

            # --- ENUM SAFE CASTING ---
            raw_stock = request.form.get("stock", "out_of_stock")
            try:
                stock_enum = StockStatus(raw_stock)
            except ValueError:
                stock_enum = StockStatus.OUT_OF_STOCK

            if price <= 0:
                flash("Variant price must be greater than 0.", "error")
                return redirect(url_for("product.product_variant_add", product_id=product_id))

            if sku:
                sku_exists = ProductVariant.query.filter_by(sku=sku).first()

                if sku_exists:
                    flash("This SKU already exists. Please use another SKU.", "error")
                    return redirect(url_for("product.product_variant_add", product_id=product_id))
            else:
                sku = None

            option_key = build_option_key(selected_options)

            duplicate_variant = ProductVariant.query.filter_by(
                product_id=product_id,
                option_key=option_key
            ).first()

            if duplicate_variant:
                flash("This variant combination already exists.", "error")
                return redirect(url_for("product.product_variant_add", product_id=product_id))

            sorted_options = sorted(
                selected_options,
                key=lambda option: option.variant_type.slug
            )

            for index, option in enumerate(sorted_options, start=1):
                exists = ProductOptionType.query.filter_by(
                    product_id=product_id,
                    variant_type_id=option.variant_type_id
                ).first()

                if not exists:
                    db.session.add(
                        ProductOptionType(
                            product_id=product_id,
                            variant_type_id=option.variant_type_id,
                            sort_order=index
                        )
                    )

            variant = ProductVariant(
                product_id=product_id,
                sku=sku,
                price=price,
                old_price=old_price,
                cost=cost,
                stock=stock_enum,  # <-- Pass Enum Object here
                status=status,
                option_key=option_key
            )

            db.session.add(variant)
            db.session.flush()

            for option in selected_options:
                db.session.add(
                    ProductVariantOption(
                        product_variant_id=variant.id,
                        variant_option_id=option.id
                    )
                )

            files = request.files.getlist("images")

            for index, file in enumerate(files, start=1):
                unique_filename = save_uploaded_file(file)

                if unique_filename:
                    db.session.add(
                        ProductVariantImage(
                            product_variant_id=variant.id,
                            image=str(unique_filename),
                            sort_order=index
                        )
                    )

            db.session.commit()

            flash("Product Variant Added Successfully", "success")
            return redirect(url_for("product.product_variant_add", product_id=product_id))

        except Exception as e:
            db.session.rollback()
            print("Variant Add Error:", e)
            flash("Error adding product variant.", "error")
            return redirect(url_for("product.product_variant_add", product_id=product_id))

    return render_template(
        "backend/admin/pages/product/variant_add.html",
        product=_product,
        variant_types=variant_types,
        existing_variants=existing_variants,
        os=os
    )


@product_bp.route("/admin/product/variant/edit/<int:variant_id>", methods=["GET", "POST"])
def product_variant_edit(variant_id):
    variant = ProductVariant.query.get_or_404(variant_id)
    _product = variant.product

    variant_types = VariantType.query.order_by(VariantType.id.asc()).all()

    if request.method == "POST":
        try:
            selected_option_ids = []

            for variant_type in variant_types:
                option_id = request.form.get(f"option_{variant_type.slug}")

                if option_id:
                    selected_option_ids.append(int(option_id))

            if not selected_option_ids:
                flash("Please select at least one variant option.", "error")
                return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

            selected_options = VariantOption.query.filter(
                VariantOption.id.in_(selected_option_ids)
            ).all()

            if len(selected_options) != len(selected_option_ids):
                flash("Some selected options are invalid.", "error")
                return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

            type_ids = [option.variant_type_id for option in selected_options]

            if len(type_ids) != len(set(type_ids)):
                flash("You can select only one option per type.", "error")
                return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

            sku = request.form.get("sku", "").strip()
            price = to_float_or_zero(request.form.get("price"))
            old_price = to_float_or_none(request.form.get("old_price"))
            cost = to_float_or_none(request.form.get("cost"))
            status = request.form.get("status", "true")

            # --- ENUM SAFE CASTING ---
            raw_stock = request.form.get("stock", "out_of_stock")
            try:
                stock_enum = StockStatus(raw_stock)
            except ValueError:
                stock_enum = StockStatus.OUT_OF_STOCK

            if price <= 0:
                flash("Variant price must be greater than 0.", "error")
                return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

            if sku:
                sku_exists = ProductVariant.query.filter(
                    ProductVariant.sku == sku,
                    ProductVariant.id != variant.id
                ).first()

                if sku_exists:
                    flash("This SKU already exists. Please use another SKU.", "error")
                    return redirect(url_for("product.product_variant_edit", variant_id=variant.id))
            else:
                sku = None

            option_key = build_option_key(selected_options)

            duplicate_variant = ProductVariant.query.filter(
                ProductVariant.product_id == _product.id,
                ProductVariant.option_key == option_key,
                ProductVariant.id != variant.id
            ).first()

            if duplicate_variant:
                flash("This variant combination already exists.", "error")
                return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

            variant.sku = sku
            variant.price = price
            variant.old_price = old_price
            variant.cost = cost
            variant.stock = stock_enum  # <-- Pass Enum Object here
            variant.status = status
            variant.option_key = option_key

            ProductVariantOption.query.filter_by(
                product_variant_id=variant.id
            ).delete()

            db.session.flush()

            for option in selected_options:
                db.session.add(
                    ProductVariantOption(
                        product_variant_id=variant.id,
                        variant_option_id=option.id
                    )
                )

                exists = ProductOptionType.query.filter_by(
                    product_id=_product.id,
                    variant_type_id=option.variant_type_id
                ).first()

                if not exists:
                    db.session.add(
                        ProductOptionType(
                            product_id=_product.id,
                            variant_type_id=option.variant_type_id,
                            sort_order=1
                        )
                    )

            files = request.files.getlist("images")

            current_max_order = db.session.query(
                db.func.max(ProductVariantImage.sort_order)
            ).filter_by(
                product_variant_id=variant.id
            ).scalar() or 0

            for index, file in enumerate(files, start=1):
                unique_filename = save_uploaded_file(file)

                if unique_filename:
                    db.session.add(
                        ProductVariantImage(
                            product_variant_id=variant.id,
                            image=str(unique_filename),
                            sort_order=current_max_order + index
                        )
                    )

            db.session.commit()

            flash("Variant updated successfully.", "success")
            return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

        except Exception as e:
            db.session.rollback()
            print("Variant Edit Error:", e)
            flash("Error updating variant.", "error")
            return redirect(url_for("product.product_variant_edit", variant_id=variant.id))

    selected_option_map = {}

    for selected in variant.selected_options:
        selected_option_map[selected.option.variant_type.slug] = selected.option.id

    return render_template(
        "backend/admin/pages/product/variant_edit.html",
        product=_product,
        variant=variant,
        variant_types=variant_types,
        selected_option_map=selected_option_map,
        os=os
    )


@product_bp.route("/admin/product/variant/delete/<int:variant_id>", methods=["POST"])
def product_variant_delete(variant_id):
    variant = ProductVariant.query.get_or_404(variant_id)
    product_id = variant.product_id

    try:
        for img in variant.images:
            delete_image_files(img.image)

        db.session.delete(variant)
        db.session.commit()

        flash("Variant Deleted Successfully", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Delete Error:", e)
        flash("Error deleting variant.", "error")

    return redirect(url_for("product.product_variant_add", product_id=product_id))


@product_bp.route("/admin/product/variant/image/delete/<int:image_id>", methods=["POST"])
def product_variant_image_delete(image_id):
    image = ProductVariantImage.query.get_or_404(image_id)
    variant_id = image.product_variant_id

    try:
        delete_image_files(image.image)

        db.session.delete(image)
        db.session.commit()

        flash("Variant image deleted successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("Variant Image Delete Error:", e)
        flash("Error deleting variant image.", "error")

    return redirect(url_for("product.product_variant_edit", variant_id=variant_id))


# ============================================================
# Bulk Best Sellers Manager
# ============================================================

@product_bp.route("/admin/product/bulk-best-seller")
def bulk_best_seller():
    categories = Category.query.order_by(Category.name.asc()).all()
    category_data = []
    for cat in categories:
        products = Product.query.filter_by(category_id=cat.id).order_by(Product.name.asc()).all()
        category_data.append({
            "category": cat,
            "products": products
        })
    return render_template(
        "backend/admin/pages/product/bulk_best_seller.html",
        category_data=category_data
    )


@product_bp.route("/admin/product/toggle-best-seller/<int:product_id>", methods=["POST"])
def toggle_best_seller(product_id):
    product = Product.query.get_or_404(product_id)
    data = request.get_json() or {}
    product.best_selling = bool(data.get("best_selling", False))
    try:
        db.session.commit()
        return {"success": True, "best_selling": product.best_selling}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "error": str(e)}, 500


@product_bp.route("/admin/product/bulk-toggle-best-seller-category", methods=["POST"])
def bulk_toggle_best_seller_category():
    data = request.get_json() or {}
    category_id = data.get("category_id")
    best_selling_val = bool(data.get("best_selling", False))
    
    if not category_id:
        return {"success": False, "error": "Category ID is required"}, 400
        
    products = Product.query.filter_by(category_id=category_id).all()
    for product in products:
        product.best_selling = best_selling_val
        
    try:
        db.session.commit()
        return {"success": True, "count": len(products)}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "error": str(e)}, 500