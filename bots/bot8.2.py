from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random
import math


class Player(BaseBot):

    def __init__(self):

        self.base_range = eval7.HandRange(
            "22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s"
        )

        self.range_weights = {}
        self.combo_cache = {}
        self.time_bank = 30.0

        # Opponent modeling
        self.opp_aggression = 0
        self.total_actions = 0

        self._initialize_range_weights()

    # -------------------------------------------------
    # INITIALIZE RANGE
    # -------------------------------------------------
    def _initialize_range_weights(self):

        self.range_weights = {}
        self.combo_cache = {}

        combos = []

        for hand in self.base_range.hands:
            cards = hand[0]
            key = (str(cards[0]), str(cards[1]))
            combos.append(key)

            self.combo_cache[key] = [
                eval7.Card(str(cards[0])),
                eval7.Card(str(cards[1]))
            ]

        weight = 1 / len(combos)

        for combo in combos:
            self.range_weights[combo] = weight

    # -------------------------------------------------
    # TIME MANAGEMENT
    # -------------------------------------------------
    def _get_iters(self, base_iters):

        if self.time_bank > 20:
            return base_iters
        elif self.time_bank > 10:
            return max(50, base_iters // 2)
        elif self.time_bank > 5:
            return max(30, base_iters // 3)
        else:
            return 15

    # -------------------------------------------------
    # CONTINUOUS BAYESIAN UPDATE
    # -------------------------------------------------
    def bayesian_update(self, board, bet_ratio, street):

        if not board:
            return

        board_eval = [eval7.Card(c) for c in board]

        strengths = []

        # Compute deterministic strength (not MC)
        for combo in self.range_weights:

            combo_cards = self.combo_cache[combo]

            # Skip impossible combos
            if any(str(c) in board for c in combo):
                continue

            score = eval7.evaluate(combo_cards + board_eval)
            strengths.append((combo, score))

        if not strengths:
            return

        strengths.sort(key=lambda x: x[1], reverse=True)

        min_score = strengths[-1][1]
        max_score = strengths[0][1]

        # Street scaling (range narrows later streets)
        if street == "flop":
            street_factor = 0.6
        elif street == "turn":
            street_factor = 0.8
        else:
            street_factor = 1.0

        # Opponent aggression factor
        if self.total_actions > 0:
            aggr_rate = self.opp_aggression / self.total_actions
        else:
            aggr_rate = 0.5

        for combo, score in strengths:

            # Normalize strength
            if max_score != min_score:
                strength_norm = (score - min_score) / (max_score - min_score)
            else:
                strength_norm = 0.5

            # Continuous likelihood model
            likelihood = (
                0.2
                + strength_norm * bet_ratio * street_factor
                + aggr_rate * (1 - strength_norm) * 0.2
            )

            self.range_weights[combo] *= likelihood

        self._normalize_range()

    # -------------------------------------------------
    # NORMALIZE
    # -------------------------------------------------
    def _normalize_range(self):

        total = sum(self.range_weights.values())

        if total == 0:
            self._initialize_range_weights()
            return

        for combo in self.range_weights:
            self.range_weights[combo] /= total

    # -------------------------------------------------
    # EQUITY VS WEIGHTED RANGE
    # -------------------------------------------------
    def compute_equity(self, my_cards, board, base_iters=150):

        iters = self._get_iters(base_iters)

        my_eval = [eval7.Card(c) for c in my_cards]
        board_eval = [eval7.Card(c) for c in board]

        wins = 0
        ties = 0
        counted = 0

        combos = list(self.range_weights.keys())
        weights = list(self.range_weights.values())

        remaining = 5 - len(board_eval)

        for _ in range(iters):

            opp_combo = random.choices(combos, weights=weights, k=1)[0]
            opp_cards = self.combo_cache[opp_combo]

            deck = eval7.Deck()
            remove = set(str(c) for c in my_eval + opp_cards + board_eval)
            deck.cards = [c for c in deck.cards if str(c) not in remove]

            deck.shuffle()
            runout = board_eval + deck.peek(remaining)

            my_score = eval7.evaluate(my_eval + runout)
            opp_score = eval7.evaluate(opp_cards + runout)

            if my_score > opp_score:
                wins += 1
            elif my_score == opp_score:
                ties += 1

            counted += 1

        if counted == 0:
            return 0.5

        return (wins + 0.5 * ties) / counted

    # -------------------------------------------------
    # HAND START
    # -------------------------------------------------
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank
        self._initialize_range_weights()

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):
        pass

    # -------------------------------------------------
    # MAIN DECISION
    # -------------------------------------------------
    def get_move(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank

        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        cost = current_state.cost_to_call
        street = current_state.street

        bet_ratio = cost / pot if pot > 0 else 0

        # Track aggression
        if cost > 0:
            self.total_actions += 1
            if bet_ratio > 0.4:
                self.opp_aggression += 1

        # Bayesian update on any bet
        if bet_ratio > 0:
            self.bayesian_update(board, bet_ratio, street)

        # PREFLOP
        if street == "pre-flop":

            equity = self.compute_equity(my_cards, [], 200)

            pot_odds = cost / (pot + cost) if pot + cost > 0 else 0

            if equity > pot_odds + 0.07:
                if equity > 0.65 and current_state.can_act(ActionRaise):
                    return ActionRaise(current_state.raise_bounds[0])
                if current_state.can_act(ActionCall):
                    return ActionCall()

            if current_state.can_act(ActionCheck):
                return ActionCheck()

            return ActionFold()

        # AUCTION (restored good logic)
        if street == "auction":

            equity = self.compute_equity(my_cards, board, 120)
            uncertainty = 1 - abs(equity - 0.5) * 2

            bid = min(pot * uncertainty * 0.75,
                      current_state.my_chips * 0.25)

            return ActionBid(int(max(0, bid)))

        # POSTFLOP
        equity = self.compute_equity(my_cards, board, 150)
        pot_odds = cost / (pot + cost) if pot + cost > 0 else 0
        spr = current_state.my_chips / pot if pot > 0 else 10

        if equity > 0.82 and current_state.can_act(ActionRaise):
            return ActionRaise(current_state.raise_bounds[1])

        if 0.4 < equity < 0.6 and current_state.can_act(ActionRaise):
            if random.random() < 0.2:
                return ActionRaise(current_state.raise_bounds[0])

        if equity > pot_odds + 0.03 and current_state.can_act(ActionCall):
            return ActionCall()

        if equity < 0.3 and current_state.can_act(ActionRaise):
            if random.random() < 0.08:
                return ActionRaise(current_state.raise_bounds[0])

        if current_state.can_act(ActionCheck):
            return ActionCheck()

        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())