"""Loose-aggressive bot (LAG/MANIAC) - plays many hands, raises frequently."""
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
            return ActionBid(int(current_state.my_chips * random.uniform(0.05, 0.25)))

        cost = current_state.cost_to_call
        pot = current_state.pot

        # Raise often
        if current_state.can_act(ActionRaise) and random.random() < 0.55:
            lo, hi = current_state.raise_bounds
            size = int(pot * random.uniform(0.5, 1.2))
            size = max(lo, min(size, hi))
            return ActionRaise(size)

        # Fold rarely
        if cost > 0 and random.random() < 0.15:
            return ActionFold()

        if current_state.can_act(ActionCall):
            return ActionCall()
        if current_state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
