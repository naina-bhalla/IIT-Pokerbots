"""Calling station bot - calls almost everything, never raises, never bluffs."""
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
            return ActionBid(random.randint(0, 5))

        cost = current_state.cost_to_call
        pot = current_state.pot

        # Only fold if facing a huge bet
        if cost > 0:
            pot_ratio = cost / pot if pot > 0 else 1
            if pot_ratio > 0.8 and random.random() < 0.3:
                return ActionFold()
            if current_state.can_act(ActionCall):
                return ActionCall()

        if current_state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
