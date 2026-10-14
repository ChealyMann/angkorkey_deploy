from models import Customer, Order, OrderItem


def test_customer_display_name(db_session):
    c = Customer(telegram_id=5, first_name="Sokha", last_name="Chan")
    assert c.display_name == "Sokha Chan"
    assert Customer(telegram_id=6, username="dara").display_name == "@dara"
    assert Customer(telegram_id=7).display_name == "#7"


def test_order_with_items_persists(db_session, make_product, make_customer):
    product, _ = make_product()
    customer = make_customer()
    order = Order(customer=customer, status="new", delivery_type="standard",
                  subtotal=10.0, total_cost=6.0, total_to_collect=12.0,
                  delivery_fee_charged=2.0, delivery_fee_driver=2.0,
                  phone="012345678", address="PP")
    order.items.append(OrderItem(product_id=product.id, name=product.name,
                                 unit_price=10.0, unit_cost=6.0, quantity=1))
    db_session.session.add(order); db_session.session.commit()
    assert Order.query.one().items[0].unit_cost == 6.0
    assert Order.__tablename__ == "orders"
