from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random
import itertools
import math


class Player(BaseBot):

    def __init__(self):

        self.base_range_str = "22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s"
        self.base_range = eval7.HandRange(self.base_range_str)

        self.range_weights = {}
        self.time_bank = 30.0

        self._initialize_range_weights()

    # INITIAL RANGE DISTRIBUTION
    def _initialize_range_weights(self):

        self.range_weights = {}

        combos = []
        for hand in self.base_range.hands:
            
            cards_tuple = hand[0]
            key = (str(cards_tuple[0]), str(cards_tuple[1]))
            combos.append(key)

        weight = 1 / len(combos) if combos else 1

        for combo in combos:
            self.range_weights[combo] = weight

    # TIME MANAGEMENT
    def _get_iters(self, base_iters):
        if self.time_bank > 20:
            return base_iters
        elif self.time_bank > 10:
            return max(40, base_iters // 2)
        elif self.time_bank > 5:
            return max(20, base_iters // 4)
        else:
            return 10

    # BAYESIAN UPDATE
    def bayesian_update_big_bet(self, board):

        board_eval = [eval7.Card(c) for c in board]

        strengths = []

        # Only update after checking time and cards
        if self.time_bank < 10 or not board:
            return

        combos = list(self.range_weights.keys())
        sample_size = min(50, len(combos))
        sampled = random.sample(combos, sample_size)

        for combo in sampled:
            combo_cards = [eval7.Card(c) for c in combo]
            
            board_strs = set(str(c) for c in board_eval)
            if any(c in board_strs for c in combo):
                self.range_weights[combo] = 0
                continue
            equity = eval7.py_hand_vs_range_monte_carlo(
                combo_cards,
                eval7.HandRange("22+,A2+,K2+,Q2+,J2+,T2+"),
                board_eval,
                20
            )
            strengths.append((combo, equity))

        if not strengths:
            return

        strengths.sort(key=lambda x: x[1], reverse=True)

        total = len(strengths)

        for i, (combo, equity) in enumerate(strengths):

            percentile = i / total

            #Likelihood model
            if percentile < 0.2:       #top 20%
                likelihood = 0.8
            elif percentile < 0.6:     #middle 40%
                likelihood = 0.3
            else:
                likelihood = 0.1

            self.range_weights[combo] *= likelihood

        self._normalize_range()

    # FILTER RANGE AFTER REVEALED CARD
    def filter_range_by_reveal(self, revealed_card):
        revealed_str = str(revealed_card)

        for combo in list(self.range_weights.keys()):
            if revealed_str not in combo:
                self.range_weights[combo] = 0

        self._normalize_range()

    # NORMALIZE DISTRIBUTION
    def _normalize_range(self):

        total = sum(self.range_weights.values())
        if total == 0:
            self._initialize_range_weights()
            return

        for combo in self.range_weights:
            self.range_weights[combo] /= total

    
    def _sample_opponent_hand(self):

        combos = list(self.range_weights.keys())
        weights = list(self.range_weights.values())

        return random.choices(combos, weights=weights, k=1)[0]

    
    def compute_equity(self, my_cards, board, base_iters=150):

        iters = self._get_iters(base_iters)

        my_cards_eval = [eval7.Card(c) for c in my_cards]
        board_eval = [eval7.Card(c) for c in board]
        used_strs = set(str(c) for c in my_cards_eval + board_eval)

        wins = 0
        ties = 0
        counted = 0

        for _ in range(iters):

            opp_combo = self._sample_opponent_hand()
            opp_cards = [eval7.Card(c) for c in opp_combo]

            if any(str(c) in used_strs for c in opp_cards):
                continue

            deck = eval7.Deck()
            remove_set = set(str(c) for c in my_cards_eval + opp_cards + board_eval)
            deck.cards = [c for c in deck.cards if str(c) not in remove_set]

            deck.shuffle()

            remaining = 5 - len(board_eval)
            runout = board_eval + deck.peek(remaining)

            my_score = eval7.evaluate(my_cards_eval + runout)
            opp_score = eval7.evaluate(opp_cards + runout)

            if my_score > opp_score:
                wins += 1
            elif my_score == opp_score:
                ties += 1
            counted += 1

        if counted == 0:
            return 0.5
        return (wins + 0.5 * ties) / counted

    # HAND START
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank
        self._initialize_range_weights()

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):
        pass

    def get_move(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank

        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        my_chips = current_state.my_chips

        # Bayesian update
        if current_state.cost_to_call > pot * 0.5:
            self.bayesian_update_big_bet(board)

        if current_state.opp_revealed_cards:
            revealed = eval7.Card(current_state.opp_revealed_cards[0])
            self.filter_range_by_reveal(revealed)

        # PREFLOP
        if current_state.street == "pre-flop":

            equity = self.compute_equity(my_cards, [], 200)

            cost = current_state.cost_to_call
            pot_odds = cost / (pot + cost) if pot + cost > 0 else 0

            if equity > pot_odds + 0.07:

                if equity > 0.65 and current_state.can_act(ActionRaise):
                    return ActionRaise(current_state.raise_bounds[0])

                if current_state.can_act(ActionCall):
                    return ActionCall()

            if current_state.can_act(ActionCheck):
                return ActionCheck()

            return ActionFold()

        # AUCTION
        if current_state.street == "auction":

            equity = self.compute_equity(my_cards, board, 120)

            uncertainty = 1 - abs(equity - 0.5) * 2

            bid = int(min(pot * uncertainty * 0.7,
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

        # Low frequency bluff
        if equity < 0.3 and current_state.can_act(ActionRaise):
            if random.random() < 0.1:
                return ActionRaise(current_state.raise_bounds[0])

        if current_state.can_act(ActionCheck):
            return ActionCheck()

        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())