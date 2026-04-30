from broker import Broker

b = Broker()
try:
    orders = b.api.get_orders()
    print(f'Recent orders: {len(orders)}')
    for order in orders[:3]:
        print(f'  {order.symbol} {order.side} {order.qty} @ {order.type} - Status: {order.status}')

    positions = b.api.get_all_positions()
    print(f'Current positions: {len(positions)}')
    for pos in positions:
        print(f'  {pos.symbol}: {pos.qty} shares')

    account = b.api.get_account()
    print(f'Account balance: ${account.cash}')
except Exception as e:
    print(f'Error: {e}')