"""Tight-passive bot (NIT) - only plays premium hands, rarely raises."""
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import random


PREMIUM = {'A', 'K', 'Q', 'J'}


class Player(BaseBot):
    def __init__(self):
        pass

    def on_hand_start(self, game_info, current_state):
        pass

    def on_hand_end(self, game_info, current_state):
        pass

    def _hand_strength(self, cards):
        ranks = [c[0] for c in cards]
        high_count = sum(1 for r in ranks if r in PREMIUM)
        paired = ranks[0] == ranks[1]
        suited = cards[0][-1] == cards[1][-1]
        score = high_count * 2 + (3 if paired else 0) + (1 if suited else 0)
        return score

    def get_move(self, game_info, current_state):
        if current_state.street == 'auction':
            return ActionBid(0)

        my_cards = current_state.my_hand
        cost = current_state.cost_to_call
        pot = current_state.pot

        if current_state.street == 'pre-flop':
            strength = self._hand_strength(my_cards)
            if strength >= 5:
                if current_state.can_act(ActionCall):
                    return ActionCall()
            if strength >= 3 and cost <= 4:
                if current_state.can_act(ActionCall):
                    return ActionCall()
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            return ActionFold()

        # Postflop: call small bets, check otherwise
        if cost > 0:
            pot_odds = cost / pot if pot > 0 else 1
            if pot_odds < 0.25 and current_state.can_act(ActionCall):
                return ActionCall()
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            return ActionFold()

        if current_state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
