from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random


class Player(BaseBot):

    def __init__(self):

        # ----------------------------
        # RANGES
        # ----------------------------
        self.base_range = eval7.HandRange("22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s")
        self.medium_range = eval7.HandRange("55+,A7+,KJ+,QJ+")
        self.tight_range = eval7.HandRange("77+,A9+,KQ+,QJ+")

        self.current_range = self.base_range

        # ----------------------------
        # CACHES
        # ----------------------------
        self.preflop_cache = {}

        # ----------------------------
        # OPPONENT MODEL
        # ----------------------------
        self.total_opp_actions = 0
        self.total_opp_aggressive = 0

        # ----------------------------
        # TIME
        # ----------------------------
        self.time_bank = 30.0

    # --------------------------------------------
    # HAND START
    # --------------------------------------------
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank
        self.current_range = self.base_range

    # --------------------------------------------
    # HAND END
    # --------------------------------------------
    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):

        # Decay aggression slowly so old history fades
        self.total_opp_aggressive *= 0.95
        self.total_opp_actions *= 0.95

    # --------------------------------------------
    # TIME CONTROL
    # --------------------------------------------
    def _get_iters(self, base_iters):

        if self.time_bank > 20:
            return base_iters
        elif self.time_bank > 10:
            return max(40, base_iters // 2)
        elif self.time_bank > 5:
            return max(20, base_iters // 4)
        else:
            return 10

    # --------------------------------------------
    # EQUITY
    # --------------------------------------------
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

    # --------------------------------------------
    # AGGRESSION FACTOR
    # --------------------------------------------
    def aggression_factor(self):

        if self.total_opp_actions == 0:
            return 1.0

        rate = self.total_opp_aggressive / self.total_opp_actions

        # Scale between 0.85 and 1.3
        return 0.85 + rate * 0.45

    # --------------------------------------------
    # RANGE ADAPTATION
    # --------------------------------------------
    def adapt_range(self, bet_ratio):

        # Multi-level tightening
        if bet_ratio > 0.75:
            self.current_range = self.tight_range
        elif bet_ratio > 0.45:
            self.current_range = self.medium_range
        else:
            self.current_range = self.base_range

    # --------------------------------------------
    # MAIN DECISION
    # --------------------------------------------
    def get_move(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank

        in_position = not current_state.is_bb
        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        my_chips = current_state.my_chips
        cost = current_state.cost_to_call

        bet_ratio = cost / pot if pot > 0 else 0

        # Track opponent aggression
        if cost > 0:
            self.total_opp_actions += 1
            if bet_ratio > 0.4:
                self.total_opp_aggressive += 1

        # Adapt range based on bet size
        self.adapt_range(bet_ratio)

        # --------------------------------------------
        # PREFLOP
        # --------------------------------------------
        if current_state.street == "pre-flop":

            key = tuple(sorted(my_cards))

            if key not in self.preflop_cache:
                equity = self.compute_equity(my_cards, [], 200)
                self.preflop_cache[key] = equity
            else:
                equity = self.preflop_cache[key]

            pot_odds = cost / (pot + cost) if pot + cost > 0 else 0

            threshold = 0.06 if in_position else 0.10

            if equity > pot_odds + threshold:

                if equity > 0.67 and current_state.can_act(ActionRaise):
                    return ActionRaise(current_state.raise_bounds[0])

                if current_state.can_act(ActionCall):
                    return ActionCall()

            if current_state.can_act(ActionCheck):
                return ActionCheck()

            return ActionFold()

        # --------------------------------------------
        # AUCTION
        # --------------------------------------------
        if current_state.street == "auction":

            equity = self.compute_equity(my_cards, board, 140)

            uncertainty = 1 - abs(equity - 0.5) * 2

            aggr_factor = self.aggression_factor()

            bid = int(min(
                pot * uncertainty * 0.75 * aggr_factor,
                my_chips * 0.25
            ))

            return ActionBid(max(0, bid))

        # --------------------------------------------
        # POSTFLOP
        # --------------------------------------------
        equity = self.compute_equity(my_cards, board, 150)

        pot_odds = cost / (pot + cost) if pot + cost > 0 else 0
        spr = my_chips / pot if pot > 0 else 10

        # Strong value
        if equity > 0.82 and current_state.can_act(ActionRaise):
            min_raise, max_raise = current_state.raise_bounds
            return ActionRaise(max_raise if spr < 2 else min_raise)

        # Semi-bluff (more in position)
        if 0.42 < equity < 0.60 and current_state.can_act(ActionRaise):
            bluff_prob = 0.30 if in_position else 0.12
            if random.random() < bluff_prob:
                return ActionRaise(current_state.raise_bounds[0])

        # Call
        if equity > pot_odds + 0.03 and current_state.can_act(ActionCall):
            return ActionCall()

        # Low equity bluff (rare)
        if equity < 0.28 and in_position and current_state.can_act(ActionRaise):
            if random.random() < 0.08:
                return ActionRaise(current_state.raise_bounds[0])

        if current_state.can_act(ActionCheck):
            return ActionCheck()

        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())