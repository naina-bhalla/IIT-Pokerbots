from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random


class Player(BaseBot):

    def __init__(self):

        self.base_range = eval7.HandRange(
            "22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s"
        )

        self.range_weights = {}
        self.combo_cache = {}

        self.hands_played = 0
        self.opp_vpip = 0
        self.opp_aggression = 0
        self.total_actions = 0
        self.opponent_archetype = "UNKNOWN"

        self.time_bank = 30.0

        self.opp_bluff_caught = 0
        self.opp_big_bets = 0

        self._initialize_range_weights()

    # ---------------- RANGE INIT ----------------

    def _initialize_range_weights(self):

        self.range_weights = {}
        self.combo_cache = {}

        combos = []

        for hand in self.base_range.hands:

            cards = hand[0]
            key = (str(cards[0]), str(cards[1]))

            combos.append(key)

            self.combo_cache[key] = {
                "cards":[eval7.Card(str(cards[0])),eval7.Card(str(cards[1]))],
                "strs":{str(cards[0]),str(cards[1])}
            }

        weight = 1/len(combos)

        for combo in combos:
            self.range_weights[combo] = weight

    # ---------------- TIME CONTROL ----------------

    def _get_iters(self, base_iters):

        if self.time_bank > 20:
            return base_iters
        if self.time_bank > 10:
            return int(base_iters * 0.75)
        if self.time_bank > 5:
            return int(base_iters * 0.5)
        if self.time_bank > 2:
            return int(base_iters * 0.25)

        return 10

    # ---------------- MONTE CARLO EQUITY ----------------

    def compute_equity(self, my_cards, board, base_iters=200):

        iters = self._get_iters(base_iters)

        my_eval = [eval7.Card(c) for c in my_cards]
        board_eval = [eval7.Card(c) for c in board]

        used_strs = set(my_cards + board)

        remaining = 5 - len(board_eval)

        full_deck = eval7.Deck().cards
        base_deck = [c for c in full_deck if str(c) not in used_strs]

        combos = list(self.range_weights.keys())
        weights = list(self.range_weights.values())

        opp_samples = random.choices(combos, weights=weights, k=iters)

        wins = 0
        ties = 0
        counted = 0

        for opp_combo in opp_samples:

            opp_cache = self.combo_cache[opp_combo]

            if not opp_cache["strs"].isdisjoint(used_strs):
                continue

            opp_cards = opp_cache["cards"]

            valid_deck = [c for c in base_deck if c not in opp_cards]

            runout = board_eval + random.sample(valid_deck, remaining)

            my_score = eval7.evaluate(my_eval + runout)
            opp_score = eval7.evaluate(opp_cards + runout)

            if my_score > opp_score:
                wins += 1
            elif my_score == opp_score:
                ties += 1

            counted += 1

        if counted == 0:
            return 0.5

        return (wins + ties * 0.5) / counted

    # ---------------- RANGE COLLAPSE ----------------

    def detect_range_collapse(self):

        threshold = 0.01

        active = 0

        for w in self.range_weights.values():
            if w > threshold:
                active += 1

        return active

    def collapse_level(self):

        combos = self.detect_range_collapse()

        if combos < 15:
            return "EXTREME"

        if combos < 40:
            return "HIGH"

        if combos < 120:
            return "MEDIUM"

        return "LOW"

    # ---------------- BOARD TEXTURE ----------------

    def board_texture(self, board):

        if len(board) < 3:
            return "UNKNOWN"

        ranks = [c[0] for c in board]
        suits = [c[1] for c in board]

        rank_vals = []
        mapping = "23456789TJQKA"

        for r in ranks:
            rank_vals.append(mapping.index(r))

        rank_vals.sort()

        if len(set(ranks)) < len(ranks):
            return "PAIRED"

        if len(set(suits)) == 1:
            return "MONOTONE"

        if max(rank_vals) - min(rank_vals) <= 4:
            return "CONNECTED"

        if max(rank_vals) >= 10:
            return "HIGH"

        return "DRY"

    # ---------------- RANGE ADVANTAGE ----------------

    def range_advantage(self, my_cards, board):

        if len(board) < 3:
            return 0

        my_equity = self.compute_equity(my_cards, board, 120)

        opp_equity = 1 - my_equity

        return my_equity - opp_equity

    # ---------------- PRESSURE INDEX ----------------

    def pressure_index(self, equity, pot, cost):

        if pot == 0:
            return equity

        pot_odds = cost / pot

        return equity - pot_odds

    # ---------------- BAYESIAN RANGE UPDATE ----------------

    def bayesian_update(self, board, bet_ratio, street):

        if not board:
            return

        board_eval = [eval7.Card(c) for c in board]
        board_strs = set(board)

        strengths = []

        for combo in list(self.range_weights.keys()):

            cache = self.combo_cache[combo]

            if not cache["strs"].isdisjoint(board_strs):
                self.range_weights[combo] = 0
                continue

            score = eval7.evaluate(cache["cards"] + board_eval)
            strengths.append((combo, score))

        if not strengths:
            return

        strengths.sort(key=lambda x:x[1], reverse=True)

        min_score = strengths[-1][1]
        max_score = strengths[0][1]

        for combo,score in strengths:

            if max_score != min_score:
                strength_norm = (score - min_score)/(max_score - min_score)
            else:
                strength_norm = 0.5

            likelihood = 0.1 + strength_norm * bet_ratio

            self.range_weights[combo] *= likelihood

        total = sum(self.range_weights.values())

        if total == 0:
            self._initialize_range_weights()
        else:
            for combo in self.range_weights:
                self.range_weights[combo] /= total

    # ---------------- HAND START ----------------

    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank

        self._initialize_range_weights()

    # ---------------- HAND END ----------------

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):

        self.hands_played += 1

        if current_state.opp_wager > 20:
            self.opp_vpip += 1

    # ---------------- MAIN DECISION ----------------

    def get_move(self, game_info: GameInfo, current_state: PokerState):

        self.time_bank = game_info.time_bank

        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        cost = current_state.cost_to_call
        street = current_state.street

        original_pot = pot - cost
        bet_ratio = cost / original_pot if original_pot > 0 else 0

        if bet_ratio > 1.2:
            self.opp_big_bets += 1

        if bet_ratio > 0:
            self.bayesian_update(board, min(bet_ratio,5.0), street)

        collapse = self.collapse_level()

        # -------- PREFLOP --------

        if street == "pre-flop":

            equity = self.compute_equity(my_cards, [], 120)
            pot_odds = cost/pot if pot>0 else 0

            if equity > pot_odds + 0.08:

                if equity > 0.65 and current_state.can_act(ActionRaise):
                    return ActionRaise(current_state.raise_bounds[0])

                if current_state.can_act(ActionCall):
                    return ActionCall()

            if current_state.can_act(ActionCheck):
                return ActionCheck()

            return ActionFold()

        # -------- AUCTION --------

        if street == "auction":

            equity = self.compute_equity(my_cards, board, 120)

            uncertainty = 1 - abs(equity - 0.5)*2
            info_value = uncertainty * original_pot

            if equity > 0.75 or equity < 0.25:
                bid = 0
            else:
                bid = info_value * 0.2

            return ActionBid(int(min(bid, current_state.my_chips * 0.1)))

        # -------- POSTFLOP --------

        equity = self.compute_equity(my_cards, board, 200)

        texture = self.board_texture(board)
        adv = self.range_advantage(my_cards, board)
        pressure = self.pressure_index(equity, pot, cost)

        min_raise,max_raise = current_state.raise_bounds if current_state.can_act(ActionRaise) else (0,0)

        # Range advantage aggression
        if adv > 0.15 and current_state.can_act(ActionRaise):

            target = int(pot * random.uniform(0.6,0.9))

            return ActionRaise(max(min_raise,min(target,max_raise)))

        # Board texture bluff
        if texture == "PAIRED" and equity < 0.45:

            if random.random() < 0.4 and current_state.can_act(ActionRaise):

                target = int(pot * 0.5)

                return ActionRaise(max(min_raise,min(target,max_raise)))

        # collapse exploitation
        if collapse == "EXTREME":

            if equity > 0.45 and current_state.can_act(ActionCall):
                return ActionCall()

        # overbet defense
        if bet_ratio > 1.5 and equity < 0.85:
            return ActionFold()

        # strong value
        if equity > 0.82 and current_state.can_act(ActionRaise):

            target = int(pot * random.uniform(0.7,1.0))

            return ActionRaise(max(min_raise,min(target,max_raise)))

        # pressure fold
        if pressure < -0.12:
            return ActionFold()

        # controlled call
        if equity > 0.55 and current_state.can_act(ActionCall):
            return ActionCall()

        if current_state.can_act(ActionCheck):
            return ActionCheck()

        return ActionFold()


if __name__ == "__main__":
    run_bot(Player(), parse_args())