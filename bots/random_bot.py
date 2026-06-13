"""Pure random bot - picks actions uniformly at random."""
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import random


class Player(BaseBot):
    def __init__(self):
        pass

    def on_hand_start(self, game_info, current_state):
        pass

    def on_hand_end(self, game_info, current_state):
        pass

    def get_move(self, game_info, current_state):
        if current_state.street == 'auction':
            return ActionBid(random.randint(0, int(current_state.my_chips * 0.15)))

        actions = []
        if current_state.can_act(ActionCheck):
            actions.append('check')
        if current_state.can_act(ActionCall):
            actions.append('call')
        if current_state.can_act(ActionFold):
            actions.append('fold')
        if current_state.can_act(ActionRaise):
            actions.append('raise')

        choice = random.choice(actions) if actions else 'fold'

        if choice == 'check':
            return ActionCheck()
        elif choice == 'call':
            return ActionCall()
        elif choice == 'fold':
            return ActionFold()
        elif choice == 'raise':
            lo, hi = current_state.raise_bounds
            return ActionRaise(random.randint(lo, min(lo + 50, hi)))

        return ActionFold()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
