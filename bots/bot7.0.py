from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random


class Player(BaseBot):

    def __init__(self):
        # Base wide range
        self.base_range_str = "22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s"
        self.tight_range_str = "77+,A9+,KQ+,QJ+"
        self.base_range = eval7.HandRange(self.base_range_str)
        self.current_range = self.base_range

        self.opp_aggression = 0
        self.hands_seen = 0
        self.time_bank = 30.0

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):
        self.time_bank = game_info.time_bank
        self.current_range = self.base_range


    # EQUITY COMPUTATION
    def _get_iters(self, base_iters):
        if self.time_bank > 20:
            return base_iters
        elif self.time_bank > 10:
            return max(30, base_iters // 2)
        elif self.time_bank > 5:
            return max(20, base_iters // 4)
        else:
            return 10

    def compute_equity(self, my_cards, board, iters=150):
        my_cards_eval = [eval7.Card(c) for c in my_cards]
        board_eval = [eval7.Card(c) for c in board]
        iters = self._get_iters(iters)

        return eval7.py_hand_vs_range_monte_carlo(
            my_cards_eval,
            self.current_range,
            board_eval,
            iters
        )


    # RANGE ADAPTATION
    def tighten_range(self):
        self.current_range = eval7.HandRange(self.tight_range_str)

    def loosen_range(self):
        self.current_range = self.base_range


    # ACTION TRACKING
    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):

        # If showdown happened, we saw full hand
        if current_state.opp_revealed_cards:
            self.hands_seen += 1


    # MAIN DECISION
    def get_move(self, game_info: GameInfo, current_state: PokerState):

        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        my_chips = current_state.my_chips
        self.time_bank = game_info.time_bank

        # Reset range each decision
        self.current_range = self.base_range

        # Detect aggression and tighten range
        if current_state.cost_to_call > pot * 0.5:
            self.tighten_range()

        # PREFLOP
        if current_state.street == "pre-flop":

            equity = self.compute_equity(my_cards, [], 200)

            cost = current_state.cost_to_call
            pot_odds = cost / (pot + cost) if pot + cost > 0 else 0

            if equity > pot_odds + 0.08:

                if equity > 0.65 and current_state.can_act(ActionRaise):
                    min_raise, max_raise = current_state.raise_bounds
                    return ActionRaise(min_raise)

                if current_state.can_act(ActionCall):
                    return ActionCall()

            if current_state.can_act(ActionCheck):
                return ActionCheck()

            return ActionFold()

        # AUCTION
        if current_state.street == "auction":

            equity = self.compute_equity(my_cards, board, 150)

            uncertainty = 1 - abs(equity - 0.5) * 2
            aggression_factor = 1.2 if self.opp_aggression > 5 else 1

            bid = int(min(pot * uncertainty * 0.7 * aggression_factor,
                          my_chips * 0.25))

            return ActionBid(max(0, bid))

        # POSTFLOP
        equity = self.compute_equity(my_cards, board, 150)

        cost = current_state.cost_to_call
        pot_odds = cost / (pot + cost) if pot + cost > 0 else 0
        spr = my_chips / pot if pot > 0 else 10

        # Value raise
        if equity > 0.8 and current_state.can_act(ActionRaise):
            min_raise, max_raise = current_state.raise_bounds
            return ActionRaise(max_raise if spr < 2 else min_raise)

        # Semi-bluff
        if 0.4 < equity < 0.6 and current_state.can_act(ActionRaise):
            if random.random() < 0.25:
                return ActionRaise(current_state.raise_bounds[0])

        # Call
        if equity > pot_odds + 0.03 and current_state.can_act(ActionCall):
            return ActionCall()

        # Bluff low freq
        if equity < 0.3 and current_state.can_act(ActionRaise):
            if random.random() < 0.1:
                return ActionRaise(current_state.raise_bounds[0])

        if current_state.can_act(ActionCheck):
            return ActionCheck()

        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())