from datetime import datetime
from extensions import db

ORDER_STATUSES = ("new", "confirmed", "packed", "delivering", "delivered", "cancelled")


class Order(db.Model):
    # Plural table name: "order" is an SQL keyword.
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), index=True, nullable=False)
    status = db.Column(db.String(16), index=True, default='new')
    delivery_type = db.Column(db.String(16))
    delivery_fee_charged = db.Column(db.Float, nullable=True)
    delivery_fee_driver = db.Column(db.Float, nullable=True)
    voucher_code = db.Column(db.String(50), nullable=True)
    subtotal = db.Column(db.Float)
    total_cost = db.Column(db.Float)
    total_to_collect = db.Column(db.Float)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    maps_link = db.Column(db.String(512))
    admin_note = db.Column(db.Text)
    cash_received = db.Column(db.Boolean, default=False)
    cash_received_at = db.Column(db.DateTime)
    notify_error = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status_changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship('Customer')
    items = db.relationship('OrderItem', backref='order', lazy=True,
                            cascade='all, delete-orphan')
    status_logs = db.relationship('OrderStatusLog', backref='order', lazy=True,
                                  cascade='all, delete-orphan',
                                  order_by='[OrderStatusLog.created_at, OrderStatusLog.id]')

    def __repr__(self):
        return '<Order %r %r>' % (self.id, self.status)


class OrderItem(db.Model):
    __tablename__ = 'order_item'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), index=True, nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'), nullable=True)
    name = db.Column(db.String(100))
    options_text = db.Column(db.String(255))
    unit_price = db.Column(db.Float)
    unit_cost = db.Column(db.Float, nullable=True)
    quantity = db.Column(db.Integer)

    def __repr__(self):
        return '<OrderItem %r x%r>' % (self.name, self.quantity)


class OrderStatusLog(db.Model):
    __tablename__ = 'order_status_log'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), index=True, nullable=False)
    from_status = db.Column(db.String(16))
    to_status = db.Column(db.String(16))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return '<OrderStatusLog %r %r->%r>' % (self.order_id, self.from_status, self.to_status)
