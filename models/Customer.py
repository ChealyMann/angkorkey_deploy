from datetime import datetime
from extensions import db


class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, index=True, nullable=False)
    first_name = db.Column(db.String(128))
    last_name = db.Column(db.String(128))
    username = db.Column(db.String(64))
    photo_url = db.Column(db.String(512))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    maps_link = db.Column(db.String(512))
    can_message = db.Column(db.Boolean, default=False, nullable=False)
    lang = db.Column(db.String(2), default='en', nullable=False)  # language used for bot messages
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)

    @property
    def display_name(self):
        if self.first_name:
            return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name
        if self.username:
            return "@" + self.username
        return f"#{self.telegram_id}"

    def __repr__(self):
        return '<Customer %r>' % self.telegram_id
