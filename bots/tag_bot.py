"""Tight-aggressive bot (TAG) - plays few hands but bets/raises them hard."""
from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot
import eval7
import random


class Player(BaseBot):
    def __init__(self):
        pass

    def on_hand_start(self, game_info, current_state):
        pass

    def on_hand_end(self, game_info, current_state):
        pass

    def _quick_equity(self, my_cards, board, iters=100):
        my_eval = [eval7.Card(c) for c in my_cards]
        board_eval = [eval7.Card(c) for c in board]
        used = set(my_cards + board)
        deck = [c for c in eval7.Deck().cards if str(c) not in used]
        remaining = 5 - len(board_eval)
        wins = 0
        for _ in range(iters):
            sample = random.sample(deck, remaining + 2)
            opp = sample[:2]
            runout = board_eval + sample[2:2 + remaining]
            my_score = eval7.evaluate(my_eval + runout)
            opp_score = eval7.evaluate(opp + runout)
            if my_score > opp_score:
                wins += 1
            elif my_score == opp_score:
                wins += 0.5
        return wins / iters

    def get_move(self, game_info, current_state):
        if current_state.street == 'auction':
            return ActionBid(int(current_state.my_chips * 0.08))

        my_cards = current_state.my_hand
        board = current_state.board or []
        cost = current_state.cost_to_call
        pot = current_state.pot
        pot_odds = cost / pot if pot > 0 else 0

        if current_state.street == 'pre-flop':
            ranks = [c[0] for c in my_cards]
            high = {'A', 'K', 'Q', 'J', 'T'}
            high_count = sum(1 for r in ranks if r in high)
            paired = ranks[0] == ranks[1]

            if paired or high_count == 2:
                if current_state.can_act(ActionRaise):
                    lo, hi = current_state.raise_bounds
                    return ActionRaise(max(lo, min(int(pot * 0.75), hi)))
                if current_state.can_act(ActionCall):
                    return ActionCall()
            if high_count >= 1 and cost <= 4:
                if current_state.can_act(ActionCall):
                    return ActionCall()
            if current_state.can_act(ActionCheck):
                return ActionCheck()
            return ActionFold()

        # Postflop
        equity = self._quick_equity(my_cards, board, 80)

        if equity > 0.7 and current_state.can_act(ActionRaise):
            lo, hi = current_state.raise_bounds
            size = int(pot * random.uniform(0.6, 0.9))
            return ActionRaise(max(lo, min(size, hi)))

        if equity > 0.5 and current_state.can_act(ActionRaise) and cost == 0:
            lo, hi = current_state.raise_bounds
            size = int(pot * random.uniform(0.3, 0.5))
            return ActionRaise(max(lo, min(size, hi)))

        if equity > pot_odds + 0.05 and current_state.can_act(ActionCall):
            return ActionCall()

        if current_state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
