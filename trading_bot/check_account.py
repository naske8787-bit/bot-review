from broker import Broker

try:
    b = Broker()
    account = b.api.get_account()
    print(f'Account Balance: ${account.cash}')
    print(f'Buying Power: ${account.buying_power}')
    positions = b.api.get_all_positions()
    print(f'Current Positions: {len(positions)}')
    for pos in positions:
        print(f'  {pos.symbol}: {pos.qty} shares')
except Exception as e:
    print(f'Error: {e}')