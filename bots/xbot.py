from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random


class Player(BaseBot):
    def __init__(self):
        # RANGE MANAGEMENT
        self.base_range = eval7.HandRange("22+,A2+,K2+,Q2+,J2+,T2+,98s,87s,76s")
        self.range_weights = {}
        self.combo_cache = {}
        
        # TIME MANAGEMENT 
        self.time_bank = 30.0
        
        # EXPLOITATIVE PROFILING 
        self.hands_played = 0
        self.opp_vpip = 0              
        self.opp_aggression = 0        
        self.total_actions = 0
        self.opponent_archetype = "UNKNOWN"
        
        # BOARD STATE 
        self.last_street = None
        self.streets_seen = 0
        
        # Load preflop equity dictionary 
        self.preflop_equities = self._get_preflop_equities()
        self._initialize_range_weights()


    # RANGE INITIALIZATION & CACHING
    def _initialize_range_weights(self):
        
        self.range_weights = {}
        self.combo_cache = {}
        combos = []

        for hand in self.base_range.hands:
            cards = hand[0]
            key = (str(cards[0]), str(cards[1]))
            combos.append(key)
            
            
            self.combo_cache[key] = {
                'cards': [eval7.Card(str(cards[0])), eval7.Card(str(cards[1]))],
                'str_set': {str(cards[0]), str(cards[1])}
            }

        weight = 1 / len(combos) if combos else 1
        for combo in combos:
            self.range_weights[combo] = weight


    # TIME BANK MANAGEMENT 
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


    # MONTE CARLO EQUITY CALCULATION - Fast + Accurate
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
        
        if sum(weights) == 0:
            self._initialize_range_weights()
            weights = list(self.range_weights.values())

        opp_samples = random.choices(combos, weights=weights, k=iters)

        wins = ties = counted = 0

        for opp_combo in opp_samples:
            opp_cache = self.combo_cache[opp_combo]
            
            if not opp_cache['str_set'].isdisjoint(used_strs):
                continue

            opp_cards = opp_cache['cards']

            if remaining == 0:
                my_score = eval7.evaluate(my_eval + board_eval)
                opp_score = eval7.evaluate(opp_cards + board_eval)
            else:
                
                valid_deck = [c for c in base_deck if c != opp_cards[0] and c != opp_cards[1]]
                runout = board_eval + random.sample(valid_deck, remaining)
                my_score = eval7.evaluate(my_eval + runout)
                opp_score = eval7.evaluate(opp_cards + runout)

            if my_score > opp_score:
                wins += 1
            elif my_score == opp_score:
                ties += 1
            counted += 1

        return (wins + 0.5 * ties) / counted if counted > 0 else 0.5


    # BAYESIAN RANGE UPDATING 
    def bayesian_update(self, board, true_bet_ratio, street):
        if not board:
            return

        board_eval = [eval7.Card(c) for c in board]
        board_strs = set(board)
        strengths = []

        for combo in list(self.range_weights.keys()):
            opp_cache = self.combo_cache[combo]
            
            if not opp_cache['str_set'].isdisjoint(board_strs):
                self.range_weights[combo] = 0
                continue
                
            score = eval7.evaluate(opp_cache['cards'] + board_eval)
            strengths.append((combo, score))

        if not strengths:
            return

        strengths.sort(key=lambda x: x[1], reverse=True)
        min_score = strengths[-1][1]
        max_score = strengths[0][1]

        street_factor = {"flop": 0.6, "turn": 0.8}.get(street, 1.0)
        aggr_rate = self.opp_aggression / self.total_actions if self.total_actions > 0 else 0.5

        for combo, score in strengths:
            strength_norm = (score - min_score) / (max_score - min_score) if max_score != min_score else 0.5
    
            likelihood = (0.1 + strength_norm * true_bet_ratio * street_factor 
                         + aggr_rate * (1 - strength_norm) * 0.2)
            self.range_weights[combo] *= likelihood

        total = sum(self.range_weights.values())
        if total == 0:
            self._initialize_range_weights()
        else:
            for combo in self.range_weights:
                self.range_weights[combo] /= total


    # OPPONENT PROFILING 
    def on_hand_start(self, game_info: GameInfo, current_state: PokerState):
        
        self.time_bank = game_info.time_bank
        self._initialize_range_weights()
        self.opponent_archetype = "UNKNOWN"

        # After 30 hands, classify opponent
        if self.hands_played > 30:
            vpip_rate = self.opp_vpip / self.hands_played
            aggression_rate = self.opp_aggression / self.total_actions if self.total_actions > 0 else 0.5
            
            if vpip_rate < 0.25:
                self.opponent_archetype = "NIT"           # Tight passive
            elif vpip_rate > 0.65:
                self.opponent_archetype = "MANIAC"        # Loose aggressive
            elif aggression_rate < 0.15:
                self.opponent_archetype = "STATION"       # Loose passive (calls everything)

    def on_hand_end(self, game_info: GameInfo, current_state: PokerState):
        
        self.hands_played += 1
        time_used = 30 - game_info.time_bank
        print(f"Round {self.hands_played} | Time used: {time_used:.2f}s | Archetype: {self.opponent_archetype}")

        # Track VPIP (voluntarily put $ in pot)
        if current_state.opp_wager > 20:
            self.opp_vpip += 1

    
    # PREFLOP STRATEGY - Lookup table based
    
    def get_move(self, game_info: GameInfo, current_state: PokerState):
        self.time_bank = game_info.time_bank
        self.last_street = current_state.street

        my_cards = current_state.my_hand
        board = current_state.board or []
        pot = current_state.pot
        cost = current_state.cost_to_call
        street = current_state.street

        original_pot = pot - cost
        true_bet_ratio = cost / original_pot if original_pot > 0 else 0

        if cost > 0:
            self.total_actions += 1
            if true_bet_ratio > 0.5:
                self.opp_aggression += 1

        
        if true_bet_ratio > 0:
            self.bayesian_update(board, min(5.0, true_bet_ratio), street)

        # ====== PREFLOP ======
        if street == "pre-flop":
            return self._preflop_strategy(current_state, my_cards, cost, pot)

        # ====== AUCTION ======
        if street == "auction":
            return self._auction_strategy(current_state, my_cards, board, original_pot)

        # ====== POSTFLOP ======
        return self._postflop_strategy(current_state, my_cards, board, cost, pot, original_pot)


    # PREFLOP STRATEGY HELPER
    def _preflop_strategy(self, current_state, my_cards, cost, pot):
        
        my_cards_str = sorted([str(c) for c in my_cards])
        dict_key = ",".join(my_cards_str)
        equity = self.preflop_equities.get(dict_key, 0.5)
        pot_odds = cost / pot if pot > 0 else 0

        if self.opponent_archetype == "NIT" and cost == 0 and equity > 0.45:
            if current_state.can_act(ActionRaise):
                return ActionRaise(current_state.raise_bounds[0])

        
        if equity > pot_odds + 0.07:
            if equity > 0.65 and current_state.can_act(ActionRaise):
                return ActionRaise(current_state.raise_bounds[0])
            if current_state.can_act(ActionCall):
                return ActionCall()

        # check if available, else fold
        if current_state.can_act(ActionCheck):
            return ActionCheck()
        return ActionFold()


    # AUCTION STRATEGY HELPER
    def _auction_strategy(self, current_state, my_cards, board, original_pot):
        equity = self.compute_equity(my_cards, board, 120)

        # Strong equity: bid more 
        if equity > 0.75:
            bid = original_pot * 0.5
        elif equity > 0.5:
            bid = original_pot * 0.2
        else:
            bid = 0

        return ActionBid(int(min(bid, current_state.my_chips * 0.2)))


    # POSTFLOP STRATEGY HELPER
    def _postflop_strategy(self, current_state, my_cards, board, cost, pot, original_pot):
        
        equity = self.compute_equity(my_cards, board, 200)
        pot_odds = cost / pot if pot > 0 else 0
        spr = min(current_state.my_chips, current_state.opp_chips) / original_pot if original_pot > 0 else 10
        min_raise, max_raise = current_state.raise_bounds if current_state.can_act(ActionRaise) else (0, 0)
        
        true_bet_ratio = cost / original_pot if original_pot > 0 else 0
        is_betting_war = cost > (original_pot * 0.4)

        
        if self.opponent_archetype == "MANIAC":
            if true_bet_ratio > 1.5 and equity < 0.88:
                return ActionFold()
            if equity > 0.90 and current_state.can_act(ActionCall):
                return ActionCall()

        
        if self.opponent_archetype == "STATION":
            if equity > 0.85 and current_state.can_act(ActionRaise):
                target = int(pot * random.uniform(1.2, 1.5))
                return ActionRaise(max(min_raise, min(target, max_raise)))

        
        if self.opponent_archetype == "NIT" and not is_betting_war:
            if equity < 0.4 and current_state.can_act(ActionRaise) and random.random() < 0.4:
                target = int(pot * random.uniform(0.35, 0.45))
                return ActionRaise(max(min_raise, min(target, max_raise)))

        # POSTFLOP STRATEGY

        if equity > 0.82 and current_state.can_act(ActionRaise):
            target = int(pot * random.uniform(0.7, 1.0))
            return ActionRaise(max(min_raise, min(target, max_raise)))

        if equity > pot_odds and equity > 0.55 and current_state.can_act(ActionCall):
            return ActionCall()

        if true_bet_ratio > 1.5 and equity < 0.85:
            return ActionFold()


        if equity < pot_odds - 0.05:
            return ActionFold()

        # Check if available, else call, else fold
        if current_state.can_act(ActionCheck):
            return ActionCheck()
        if current_state.can_act(ActionCall):
            return ActionCall()
        return ActionFold()
    
    def _get_preflop_equities(self):
        return {
            "2c,2d": 0.5073, "2c,2h": 0.5073, "2c,2s": 0.5073,
            "2c,3c": 0.4249, "2c,3d": 0.4157, "2c,3h": 0.4184,
            "2c,3s": 0.4186, "2c,4c": 0.4218, "2c,4d": 0.4174,
            "2c,4h": 0.4175, "2c,4s": 0.4239, "2c,5c": 0.4235,
            "2c,5d": 0.4159, "2c,5h": 0.4131, "2c,5s": 0.4235,
            "2c,6c": 0.4281, "2c,6d": 0.4142, "2c,6h": 0.4188,
            "2c,6s": 0.4247, "2c,7c": 0.4328, "2c,7d": 0.4183,
            "2c,7h": 0.4218, "2c,7s": 0.4275, "2c,8c": 0.4372,
            "2c,8d": 0.4236, "2c,8h": 0.4246, "2c,8s": 0.431,
            "2c,9c": 0.4433, "2c,9d": 0.4309, "2c,9h": 0.4307,
            "2c,9s": 0.4358, "2c,Tc": 0.4497, "2c,Td": 0.4361,
            "2c,Th": 0.4349, "2c,Ts": 0.4385, "2c,Jc": 0.4565,
            "2c,Jd": 0.4422, "2c,Jh": 0.4423, "2c,Js": 0.4461,
            "2c,Qc": 0.464, "2c,Qd": 0.4509, "2c,Qh": 0.4522,
            "2c,Qs": 0.4549, "2c,Kc": 0.4724, "2c,Kd": 0.4605,
            "2c,Kh": 0.4626, "2c,Ks": 0.4657, "2c,Ac": 0.4834,
            "2c,Ad": 0.4755, "2c,Ah": 0.482, "2c,As": 0.4799,
            "2d,2h": 0.5073, "2d,2s": 0.5073, "2d,3c": 0.4186,
            "2d,3d": 0.4157, "2d,3h": 0.4207, "2d,3s": 0.4157,
            "2d,4c": 0.4239, "2d,4d": 0.4174, "2d,4h": 0.4227,
            "2d,4s": 0.4174, "2d,5c": 0.4235, "2d,5d": 0.4159,
            "2d,5h": 0.4184, "2d,5s": 0.4159, "2d,6c": 0.4247,
            "2d,6d": 0.4142, "2d,6h": 0.4239, "2d,6s": 0.4142,
            "2d,7c": 0.4275, "2d,7d": 0.4183, "2d,7h": 0.4272,
            "2d,7s": 0.4183, "2d,8c": 0.431, "2d,8d": 0.4236,
            "2d,8h": 0.4301, "2d,8s": 0.4236, "2d,9c": 0.4358,
            "2d,9d": 0.4309, "2d,9h": 0.4365, "2d,9s": 0.4309,
            "2d,Tc": 0.4385, "2d,Td": 0.4361, "2d,Th": 0.4432,
            "2d,Ts": 0.4361, "2d,Jc": 0.4461, "2d,Jd": 0.4422,
            "2d,Jh": 0.4476, "2d,Js": 0.4422, "2d,Qc": 0.4549,
            "2d,Qd": 0.4509, "2d,Qh": 0.4571, "2d,Qs": 0.4509,
            "2d,Kc": 0.4657, "2d,Kd": 0.4605, "2d,Kh": 0.4678,
            "2d,Ks": 0.4605, "2d,Ac": 0.4799, "2d,Ad": 0.4755,
            "2d,Ah": 0.4874, "2d,As": 0.4755, "2h,2s": 0.5073,
            "2h,3c": 0.4184, "2h,3d": 0.4207, "2h,3h": 0.4184,
            "2h,3s": 0.4207, "2h,4c": 0.4175, "2h,4d": 0.4227,
            "2h,4h": 0.4175, "2h,4s": 0.4227, "2h,5c": 0.4131,
            "2h,5d": 0.4184, "2h,5h": 0.4131, "2h,5s": 0.4184,
            "2h,6c": 0.4188, "2h,6d": 0.4239, "2h,6h": 0.4188,
            "2h,6s": 0.4239, "2h,7c": 0.4218, "2h,7d": 0.4272,
            "2h,7h": 0.4218, "2h,7s": 0.4272, "2h,8c": 0.4246,
            "2h,8d": 0.4301, "2h,8h": 0.4246, "2h,8s": 0.4301,
            "2h,9c": 0.4307, "2h,9d": 0.4365, "2h,9h": 0.4307,
            "2h,9s": 0.4365, "2h,Tc": 0.4349, "2h,Td": 0.4432,
            "2h,Th": 0.4349, "2h,Ts": 0.4432, "2h,Jc": 0.4423,
            "2h,Jd": 0.4476, "2h,Jh": 0.4423, "2h,Js": 0.4476,
            "2h,Qc": 0.4522, "2h,Qd": 0.4571, "2h,Qh": 0.4522,
            "2h,Qs": 0.4571, "2h,Kc": 0.4626, "2h,Kd": 0.4678,
            "2h,Kh": 0.4626, "2h,Ks": 0.4678, "2h,Ac": 0.482,
            "2h,Ad": 0.4874, "2h,Ah": 0.482, "2h,As": 0.4874,
            "2s,3c": 0.4186, "2s,3d": 0.4157, "2s,3h": 0.4207,
            "2s,3s": 0.4186, "2s,4c": 0.4239, "2s,4d": 0.4174,
            "2s,4h": 0.4227, "2s,4s": 0.4239, "2s,5c": 0.4235,
            "2s,5d": 0.4159, "2s,5h": 0.4184, "2s,5s": 0.4235,
            "2s,6c": 0.4247, "2s,6d": 0.4142, "2s,6h": 0.4239,
            "2s,6s": 0.4247, "2s,7c": 0.4275, "2s,7d": 0.4183,
            "2s,7h": 0.4272, "2s,7s": 0.4275, "2s,8c": 0.431,
            "2s,8d": 0.4236, "2s,8h": 0.4301, "2s,8s": 0.431,
            "2s,9c": 0.4358, "2s,9d": 0.4309, "2s,9h": 0.4365,
            "2s,9s": 0.4358, "2s,Tc": 0.4385, "2s,Td": 0.4361,
            "2s,Th": 0.4432, "2s,Ts": 0.4385, "2s,Jc": 0.4461,
            "2s,Jd": 0.4422, "2s,Jh": 0.4476, "2s,Js": 0.4461,
            "2s,Qc": 0.4549, "2s,Qd": 0.4509, "2s,Qh": 0.4571,
            "2s,Qs": 0.4549, "2s,Kc": 0.4657, "2s,Kd": 0.4605,
            "2s,Kh": 0.4678, "2s,Ks": 0.4657, "2s,Ac": 0.4799,
            "2s,Ad": 0.4755, "2s,Ah": 0.4874, "2s,As": 0.4799,
            "3c,3d": 0.5218, "3c,3h": 0.5218, "3c,3s": 0.5218,
            "3c,4c": 0.4374, "3c,4d": 0.4276, "3c,4h": 0.4308,
            "3c,4s": 0.4305, "3c,5c": 0.4385, "3c,5d": 0.4302,
            "3c,5h": 0.4271, "3c,5s": 0.4342, "3c,6c": 0.4439,
            "3c,6d": 0.4307, "3c,6h": 0.4324, "3c,6s": 0.4398,
            "3c,7c": 0.4503, "3c,7d": 0.4365, "3c,7h": 0.4372,
            "3c,7s": 0.4447, "3c,8c": 0.4576, "3c,8d": 0.4451,
            "3c,8h": 0.4448, "3c,8s": 0.4522, "3c,9c": 0.4663,
            "3c,9d": 0.4553, "3c,9h": 0.454, "3c,9s": 0.4604,
            "3c,Tc": 0.4748, "3c,Td": 0.4639, "3c,Th": 0.4625,
            "3c,Ts": 0.469, "3c,Jc": 0.483, "3c,Jd": 0.4719,
            "3c,Jh": 0.4703, "3c,Js": 0.4767, "3c,Qc": 0.491,
            "3c,Qd": 0.4815, "3c,Qh": 0.4808, "3c,Qs": 0.4851,
            "3c,Kc": 0.5005, "3c,Kd": 0.4912, "3c,Kh": 0.4917,
            "3c,Ks": 0.4945, "3c,Ac": 0.5123, "3c,Ad": 0.5063,
            "3c,Ah": 0.5145, "3c,As": 0.5115, "3d,3h": 0.5218,
            "3d,3s": 0.5218, "3d,4c": 0.4305, "3d,4d": 0.4276,
            "3d,4h": 0.4331, "3d,4s": 0.4276, "3d,5c": 0.4342,
            "3d,5d": 0.4302, "3d,5h": 0.4296, "3d,5s": 0.4302,
            "3d,6c": 0.4398, "3d,6d": 0.4307, "3d,6h": 0.4346,
            "3d,6s": 0.4307, "3d,7c": 0.4447, "3d,7d": 0.4365,
            "3d,7h": 0.4397, "3d,7s": 0.4365, "3d,8c": 0.4522,
            "3d,8d": 0.4451, "3d,8h": 0.4479, "3d,8s": 0.4451,
            "3d,9c": 0.4604, "3d,9d": 0.4553, "3d,9h": 0.4569,
            "3d,9s": 0.4553, "3d,Tc": 0.469, "3d,Td": 0.4639,
            "3d,Th": 0.4666, "3d,Ts": 0.4639, "3d,Jc": 0.4767,
            "3d,Jd": 0.4719, "3d,Jh": 0.4734, "3d,Js": 0.4719,
            "3d,Qc": 0.4851, "3d,Qd": 0.4815, "3d,Qh": 0.4839,
            "3d,Qs": 0.4815, "3d,Kc": 0.4945, "3d,Kd": 0.4912,
            "3d,Kh": 0.4948, "3d,Ks": 0.4912, "3d,Ac": 0.5115,
            "3d,Ad": 0.5063, "3d,Ah": 0.5197, "3d,As": 0.5063,
            "3h,3s": 0.5218, "3h,4c": 0.4308, "3h,4d": 0.4331,
            "3h,4h": 0.4308, "3h,4s": 0.4331, "3h,5c": 0.4271,
            "3h,5d": 0.4296, "3h,5h": 0.4271, "3h,5s": 0.4296,
            "3h,6c": 0.4324, "3h,6d": 0.4346, "3h,6h": 0.4324,
            "3h,6s": 0.4346, "3h,7c": 0.4372, "3h,7d": 0.4397,
            "3h,7h": 0.4372, "3h,7s": 0.4397, "3h,8c": 0.4448,
            "3h,8d": 0.4479, "3h,8h": 0.4448, "3h,8s": 0.4479,
            "3h,9c": 0.454, "3h,9d": 0.4569, "3h,9h": 0.454,
            "3h,9s": 0.4569, "3h,Tc": 0.4625, "3h,Td": 0.4666,
            "3h,Th": 0.4625, "3h,Ts": 0.4666, "3h,Jc": 0.4703,
            "3h,Jd": 0.4734, "3h,Jh": 0.4703, "3h,Js": 0.4734,
            "3h,Qc": 0.4808, "3h,Qd": 0.4839, "3h,Qh": 0.4808,
            "3h,Qs": 0.4839, "3h,Kc": 0.4917, "3h,Kd": 0.4948,
            "3h,Kh": 0.4917, "3h,Ks": 0.4948, "3h,Ac": 0.5145,
            "3h,Ad": 0.5197, "3h,Ah": 0.5145, "3h,As": 0.5197,
            "3s,4c": 0.4305, "3s,4d": 0.4276, "3s,4h": 0.4331,
            "3s,4s": 0.4305, "3s,5c": 0.4342, "3s,5d": 0.4302,
            "3s,5h": 0.4296, "3s,5s": 0.4342, "3s,6c": 0.4398,
            "3s,6d": 0.4307, "3s,6h": 0.4346, "3s,6s": 0.4398,
            "3s,7c": 0.4447, "3s,7d": 0.4365, "3s,7h": 0.4397,
            "3s,7s": 0.4447, "3s,8c": 0.4522, "3s,8d": 0.4451,
            "3s,8h": 0.4479, "3s,8s": 0.4522, "3s,9c": 0.4604,
            "3s,9d": 0.4553, "3s,9h": 0.4569, "3s,9s": 0.4604,
            "3s,Tc": 0.469, "3s,Td": 0.4639, "3s,Th": 0.4666,
            "3s,Ts": 0.469, "3s,Jc": 0.4767, "3s,Jd": 0.4719,
            "3s,Jh": 0.4734, "3s,Js": 0.4767, "3s,Qc": 0.4851,
            "3s,Qd": 0.4815, "3s,Qh": 0.4839, "3s,Qs": 0.4851,
            "3s,Kc": 0.4945, "3s,Kd": 0.4912, "3s,Kh": 0.4948,
            "3s,Ks": 0.4945, "3s,Ac": 0.5115, "3s,Ad": 0.5063,
            "3s,Ah": 0.5197, "3s,As": 0.5115, "4c,4d": 0.5345,
            "4c,4h": 0.5345, "4c,4s": 0.5345, "4c,5c": 0.4524,
            "4c,5d": 0.4413, "4c,5h": 0.4415, "4c,5s": 0.4495,
            "4c,6c": 0.4608, "4c,6d": 0.4456, "4c,6h": 0.4466,
            "4c,6s": 0.4558, "4c,7c": 0.4699, "4c,7d": 0.4526,
            "4c,7h": 0.4527, "4c,7s": 0.4616, "4c,8c": 0.4793,
            "4c,8d": 0.4636, "4c,8h": 0.4631, "4c,8s": 0.4708,
            "4c,9c": 0.4897, "4c,9d": 0.4748, "4c,9h": 0.4738,
            "4c,9s": 0.4809, "4c,Tc": 0.5006, "4c,Td": 0.4867,
            "4c,Th": 0.4854, "4c,Ts": 0.4923, "4c,Jc": 0.5103,
            "4c,Jd": 0.4959, "4c,Jh": 0.4939, "4c,Js": 0.5008,
            "4c,Qc": 0.5191, "4c,Qd": 0.5059, "4c,Qh": 0.5048,
            "4c,Qs": 0.5104, "4c,Kc": 0.5289, "4c,Kd": 0.5169,
            "4c,Kh": 0.5169, "4c,Ks": 0.5217, "4c,Ac": 0.5415,
            "4c,Ad": 0.5333, "4c,Ah": 0.5417, "4c,As": 0.5389,
            "4d,4h": 0.5345, "4d,4s": 0.5345, "4d,5c": 0.4495,
            "4d,5d": 0.4413, "4d,5h": 0.4437, "4d,5s": 0.4413,
            "4d,6c": 0.4558, "4d,6d": 0.4456, "4d,6h": 0.4488,
            "4d,6s": 0.4456, "4d,7c": 0.4616, "4d,7d": 0.4526,
            "4d,7h": 0.4549, "4d,7s": 0.4526, "4d,8c": 0.4708,
            "4d,8d": 0.4636, "4d,8h": 0.4659, "4d,8s": 0.4636,
            "4d,9c": 0.4809, "4d,9d": 0.4748, "4d,9h": 0.4768,
            "4d,9s": 0.4748, "4d,Tc": 0.4923, "4d,Td": 0.4867,
            "4d,Th": 0.4888, "4d,Ts": 0.4867, "4d,Jc": 0.5008,
            "4d,Jd": 0.4959, "4d,Jh": 0.4973, "4d,Js": 0.4959,
            "4d,Qc": 0.5104, "4d,Qd": 0.5059, "4d,Qh": 0.5077,
            "4d,Qs": 0.5059, "4d,Kc": 0.5217, "4d,Kd": 0.5169,
            "4d,Kh": 0.5195, "4d,Ks": 0.5169, "4d,Ac": 0.5389,
            "4d,Ad": 0.5333, "4d,Ah": 0.5471, "4d,As": 0.5333,
            "4h,4s": 0.5345, "4h,5c": 0.4415, "4h,5d": 0.4437,
            "4h,5h": 0.4415, "4h,5s": 0.4437, "4h,6c": 0.4466,
            "4h,6d": 0.4488, "4h,6h": 0.4466, "4h,6s": 0.4488,
            "4h,7c": 0.4527, "4h,7d": 0.4549, "4h,7h": 0.4527,
            "4h,7s": 0.4549, "4h,8c": 0.4631, "4h,8d": 0.4659,
            "4h,8h": 0.4631, "4h,8s": 0.4659, "4h,9c": 0.4738,
            "4h,9d": 0.4768, "4h,9h": 0.4738, "4h,9s": 0.4768,
            "4h,Tc": 0.4854, "4h,Td": 0.4888, "4h,Th": 0.4854,
            "4h,Ts": 0.4888, "4h,Jc": 0.4939, "4h,Jd": 0.4973,
            "4h,Jh": 0.4939, "4h,Js": 0.4973, "4h,Qc": 0.5048,
            "4h,Qd": 0.5077, "4h,Qh": 0.5048, "4h,Qs": 0.5077,
            "4h,Kc": 0.5169, "4h,Kd": 0.5195, "4h,Kh": 0.5169,
            "4h,Ks": 0.5195, "4h,Ac": 0.5417, "4h,Ad": 0.5471,
            "4h,Ah": 0.5417, "4h,As": 0.5471, "4s,5c": 0.4495,
            "4s,5d": 0.4413, "4s,5h": 0.4437, "4s,5s": 0.4495,
            "4s,6c": 0.4558, "4s,6d": 0.4456, "4s,6h": 0.4488,
            "4s,6s": 0.4558, "4s,7c": 0.4616, "4s,7d": 0.4526,
            "4s,7h": 0.4549, "4s,7s": 0.4616, "4s,8c": 0.4708,
            "4s,8d": 0.4636, "4s,8h": 0.4659, "4s,8s": 0.4708,
            "4s,9c": 0.4809, "4s,9d": 0.4748, "4s,9h": 0.4768,
            "4s,9s": 0.4809, "4s,Tc": 0.4923, "4s,Td": 0.4867,
            "4s,Th": 0.4888, "4s,Ts": 0.4923, "4s,Jc": 0.5008,
            "4s,Jd": 0.4959, "4s,Jh": 0.4973, "4s,Js": 0.5008,
            "4s,Qc": 0.5104, "4s,Qd": 0.5059, "4s,Qh": 0.5077,
            "4s,Qs": 0.5104, "4s,Kc": 0.5217, "4s,Kd": 0.5169,
            "4s,Kh": 0.5195, "4s,Ks": 0.5217, "4s,Ac": 0.5389,
            "4s,Ad": 0.5333, "4s,Ah": 0.5471, "4s,As": 0.5389,
            "5c,5d": 0.5471, "5c,5h": 0.5471, "5c,5s": 0.5471,
            "5c,6c": 0.4739, "5c,6d": 0.4586, "5c,6h": 0.4597,
            "5c,6s": 0.4685, "5c,7c": 0.4841, "5c,7d": 0.4671,
            "5c,7h": 0.4678, "5c,7s": 0.4762, "5c,8c": 0.4948,
            "5c,8d": 0.478, "5c,8h": 0.4783, "5c,8s": 0.4862,
            "5c,9c": 0.5065, "5c,9d": 0.4907, "5c,9h": 0.4907,
            "5c,9s": 0.4977, "5c,Tc": 0.5182, "5c,Td": 0.5029,
            "5c,Th": 0.5023, "5c,Ts": 0.5096, "5c,Jc": 0.5289,
            "5c,Jd": 0.5142, "5c,Jh": 0.5132, "5c,Js": 0.5199,
            "5c,Qc": 0.5386, "5c,Qd": 0.5249, "5c,Qh": 0.5244,
            "5c,Qs": 0.5304, "5c,Kc": 0.5484, "5c,Kd": 0.5361,
            "5c,Kh": 0.5366, "5c,Ks": 0.5415, "5c,Ac": 0.5617,
            "5c,Ad": 0.5533, "5c,Ah": 0.5622, "5c,As": 0.5592,
            "5d,5h": 0.5471, "5d,5s": 0.5471, "5d,6c": 0.4685,
            "5d,6d": 0.4586, "5d,6h": 0.4619, "5d,6s": 0.4586,
            "5d,7c": 0.4762, "5d,7d": 0.4671, "5d,7h": 0.4701,
            "5d,7s": 0.4671, "5d,8c": 0.4862, "5d,8d": 0.478,
            "5d,8h": 0.4811, "5d,8s": 0.478, "5d,9c": 0.4977,
            "5d,9d": 0.4907, "5d,9h": 0.4936, "5d,9s": 0.4907,
            "5d,Tc": 0.5096, "5d,Td": 0.5029, "5d,Th": 0.5057,
            "5d,Ts": 0.5029, "5d,Jc": 0.5199, "5d,Jd": 0.5142,
            "5d,Jh": 0.5161, "5d,Js": 0.5142, "5d,Qc": 0.5304,
            "5d,Qd": 0.5249, "5d,Qh": 0.5273, "5d,Qs": 0.5249,
            "5d,Kc": 0.5415, "5d,Kd": 0.5361, "5d,Kh": 0.5395,
            "5d,Ks": 0.5361, "5d,Ac": 0.5592, "5d,Ad": 0.5533,
            "5d,Ah": 0.5678, "5d,As": 0.5533, "5h,5s": 0.5471,
            "5h,6c": 0.4597, "5h,6d": 0.4619, "5h,6h": 0.4597,
            "5h,6s": 0.4619, "5h,7c": 0.4678, "5h,7d": 0.4701,
            "5h,7h": 0.4678, "5h,7s": 0.4701, "5h,8c": 0.4783,
            "5h,8d": 0.4811, "5h,8h": 0.4783, "5h,8s": 0.4811,
            "5h,9c": 0.4907, "5h,9d": 0.4936, "5h,9h": 0.4907,
            "5h,9s": 0.4936, "5h,Tc": 0.5023, "5h,Td": 0.5057,
            "5h,Th": 0.5023, "5h,Ts": 0.5057, "5h,Jc": 0.5132,
            "5h,Jd": 0.5161, "5h,Jh": 0.5132, "5h,Js": 0.5161,
            "5h,Qc": 0.5244, "5h,Qd": 0.5273, "5h,Qh": 0.5244,
            "5h,Qs": 0.5273, "5h,Kc": 0.5366, "5h,Kd": 0.5395,
            "5h,Kh": 0.5366, "5h,Ks": 0.5395, "5h,Ac": 0.5622,
            "5h,Ad": 0.5678, "5h,Ah": 0.5622, "5h,As": 0.5678,
            "5s,6c": 0.4685, "5s,6d": 0.4586, "5s,6h": 0.4619,
            "5s,6s": 0.4685, "5s,7c": 0.4762, "5s,7d": 0.4671,
            "5s,7h": 0.4701, "5s,7s": 0.4762, "5s,8c": 0.4862,
            "5s,8d": 0.478, "5s,8h": 0.4811, "5s,8s": 0.4862,
            "5s,9c": 0.4977, "5s,9d": 0.4907, "5s,9h": 0.4936,
            "5s,9s": 0.4977, "5s,Tc": 0.5096, "5s,Td": 0.5029,
            "5s,Th": 0.5057, "5s,Ts": 0.5096, "5s,Jc": 0.5199,
            "5s,Jd": 0.5142, "5s,Jh": 0.5161, "5s,Js": 0.5199,
            "5s,Qc": 0.5304, "5s,Qd": 0.5249, "5s,Qh": 0.5273,
            "5s,Qs": 0.5304, "5s,Kc": 0.5415, "5s,Kd": 0.5361,
            "5s,Kh": 0.5395, "5s,Ks": 0.5415, "5s,Ac": 0.5592,
            "5s,Ad": 0.5533, "5s,Ah": 0.5678, "5s,As": 0.5592,
            "6c,6d": 0.5572, "6c,6h": 0.5572, "6c,6s": 0.5572,
            "6c,7c": 0.4946, "6c,7d": 0.4785, "6c,7h": 0.4803,
            "6c,7s": 0.4878, "6c,8c": 0.5068, "6c,8d": 0.4901,
            "6c,8h": 0.4916, "6c,8s": 0.4986, "6c,9c": 0.5196,
            "6c,9d": 0.5034, "6c,9h": 0.5039, "6c,9s": 0.5109,
            "6c,Tc": 0.5324, "6c,Td": 0.5169, "6c,Th": 0.5171,
            "6c,Ts": 0.524, "6c,Jc": 0.5441, "6c,Jd": 0.5294,
            "6c,Jh": 0.5295, "6c,Js": 0.5361, "6c,Qc": 0.5549,
            "6c,Qd": 0.5409, "6c,Qh": 0.5408, "6c,Qs": 0.5471,
            "6c,Kc": 0.565, "6c,Kd": 0.5532, "6c,Kh": 0.5539,
            "6c,Ks": 0.5587, "6c,Ac": 0.5781, "6c,Ad": 0.5693,
            "6c,Ah": 0.5802, "6c,As": 0.5759, "6d,6h": 0.5572,
            "6d,6s": 0.5572, "6d,7c": 0.4878, "6d,7d": 0.4785,
            "6d,7h": 0.4833, "6d,7s": 0.4785, "6d,8c": 0.4986,
            "6d,8d": 0.4901, "6d,8h": 0.4947, "6d,8s": 0.4901,
            "6d,9c": 0.5109, "6d,9d": 0.5034, "6d,9h": 0.5069,
            "6d,9s": 0.5034, "6d,Tc": 0.524, "6d,Td": 0.5169,
            "6d,Th": 0.5201, "6d,Ts": 0.5169, "6d,Jc": 0.5361,
            "6d,Jd": 0.5294, "6d,Jh": 0.5325, "6d,Js": 0.5294,
            "6d,Qc": 0.5471, "6d,Qd": 0.5409, "6d,Qh": 0.5439,
            "6d,Qs": 0.5409, "6d,Kc": 0.5587, "6d,Kd": 0.5532,
            "6d,Kh": 0.5571, "6d,Ks": 0.5532, "6d,Ac": 0.5759,
            "6d,Ad": 0.5693, "6d,Ah": 0.5848, "6d,As": 0.5693,
            "6h,6s": 0.5572, "6h,7c": 0.4803, "6h,7d": 0.4833,
            "6h,7h": 0.4803, "6h,7s": 0.4833, "6h,8c": 0.4916,
            "6h,8d": 0.4947, "6h,8h": 0.4916, "6h,8s": 0.4947,
            "6h,9c": 0.5039, "6h,9d": 0.5069, "6h,9h": 0.5039,
            "6h,9s": 0.5069, "6h,Tc": 0.5171, "6h,Td": 0.5201,
            "6h,Th": 0.5171, "6h,Ts": 0.5201, "6h,Jc": 0.5295,
            "6h,Jd": 0.5325, "6h,Jh": 0.5295, "6h,Js": 0.5325,
            "6h,Qc": 0.5408, "6h,Qd": 0.5439, "6h,Qh": 0.5408,
            "6h,Qs": 0.5439, "6h,Kc": 0.5539, "6h,Kd": 0.5571,
            "6h,Kh": 0.5539, "6h,Ks": 0.5571, "6h,Ac": 0.5802,
            "6h,Ad": 0.5848, "6h,Ah": 0.5802, "6h,As": 0.5848,
            "6s,7c": 0.4878, "6s,7d": 0.4785, "6s,7h": 0.4833,
            "6s,7s": 0.4878, "6s,8c": 0.4986, "6s,8d": 0.4901,
            "6s,8h": 0.4947, "6s,8s": 0.4986, "6s,9c": 0.5109,
            "6s,9d": 0.5034, "6s,9h": 0.5069, "6s,9s": 0.5109,
            "6s,Tc": 0.524, "6s,Td": 0.5169, "6s,Th": 0.5201,
            "6s,Ts": 0.524, "6s,Jc": 0.5361, "6s,Jd": 0.5294,
            "6s,Jh": 0.5325, "6s,Js": 0.5361, "6s,Qc": 0.5471,
            "6s,Qd": 0.5409, "6s,Qh": 0.5439, "6s,Qs": 0.5471,
            "6s,Kc": 0.5587, "6s,Kd": 0.5532, "6s,Kh": 0.5571,
            "6s,Ks": 0.5587, "6s,Ac": 0.5759, "6s,Ad": 0.5693,
            "6s,Ah": 0.5848, "6s,As": 0.5759, "7c,7d": 0.5651,
            "7c,7h": 0.5651, "7c,7s": 0.5651, "7c,8c": 0.506,
            "7c,8d": 0.4902, "7c,8h": 0.4932, "7c,8s": 0.5001,
            "7c,9c": 0.5201, "7c,9d": 0.5052, "7c,9h": 0.5066,
            "7c,9s": 0.5134, "7c,Tc": 0.5341, "7c,Td": 0.5191,
            "7c,Th": 0.5199, "7c,Ts": 0.5266, "7c,Jc": 0.5472,
            "7c,Jd": 0.5323, "7c,Jh": 0.5328, "7c,Js": 0.5393,
            "7c,Qc": 0.5593, "7c,Qd": 0.5451, "7c,Qh": 0.5456,
            "7c,Qs": 0.5517, "7c,Kc": 0.5702, "7c,Kd": 0.5581,
            "7c,Kh": 0.5597, "7c,Ks": 0.5641, "7c,Ac": 0.5839,
            "7c,Ad": 0.5747, "7c,Ah": 0.5876, "7c,As": 0.5821,
            "7d,7h": 0.5651, "7d,7s": 0.5651, "7d,8c": 0.5001,
            "7d,8d": 0.4902, "7d,8h": 0.4965, "7d,8s": 0.4902,
            "7d,9c": 0.5134, "7d,9d": 0.5052, "7d,9h": 0.5098,
            "7d,9s": 0.5052, "7d,Tc": 0.5266, "7d,Td": 0.5191,
            "7d,Th": 0.5231, "7d,Ts": 0.5191, "7d,Jc": 0.5393,
            "7d,Jd": 0.5323, "7d,Jh": 0.5361, "7d,Js": 0.5323,
            "7d,Qc": 0.5517, "7d,Qd": 0.5451, "7d,Qh": 0.5488,
            "7d,Qs": 0.5451, "7d,Kc": 0.5641, "7d,Kd": 0.5581,
            "7d,Kh": 0.5629, "7d,Ks": 0.5581, "7d,Ac": 0.5821,
            "7d,Ad": 0.5747, "7d,Ah": 0.5922, "7d,As": 0.5747,
            "7h,7s": 0.5651, "7h,8c": 0.4932, "7h,8d": 0.4965,
            "7h,8h": 0.4932, "7h,8s": 0.4965, "7h,9c": 0.5066,
            "7h,9d": 0.5098, "7h,9h": 0.5066, "7h,9s": 0.5098,
            "7h,Tc": 0.5199, "7h,Td": 0.5231, "7h,Th": 0.5199,
            "7h,Ts": 0.5231, "7h,Jc": 0.5328, "7h,Jd": 0.5361,
            "7h,Jh": 0.5328, "7h,Js": 0.5361, "7h,Qc": 0.5456,
            "7h,Qd": 0.5488, "7h,Qh": 0.5456, "7h,Qs": 0.5488,
            "7h,Kc": 0.5597, "7h,Kd": 0.5629, "7h,Kh": 0.5597,
            "7h,Ks": 0.5629, "7h,Ac": 0.5876, "7h,Ad": 0.5922,
            "7h,Ah": 0.5876, "7h,As": 0.5922, "7s,8c": 0.5001,
            "7s,8d": 0.4902, "7s,8h": 0.4965, "7s,8s": 0.5001,
            "7s,9c": 0.5134, "7s,9d": 0.5052, "7s,9h": 0.5098,
            "7s,9s": 0.5134, "7s,Tc": 0.5266, "7s,Td": 0.5191,
            "7s,Th": 0.5231, "7s,Ts": 0.5266, "7s,Jc": 0.5393,
            "7s,Jd": 0.5323, "7s,Jh": 0.5361, "7s,Js": 0.5393,
            "7s,Qc": 0.5517, "7s,Qd": 0.5451, "7s,Qh": 0.5488,
            "7s,Qs": 0.5517, "7s,Kc": 0.5641, "7s,Kd": 0.5581,
            "7s,Kh": 0.5629, "7s,Ks": 0.5641, "7s,Ac": 0.5821,
            "7s,Ad": 0.5747, "7s,Ah": 0.5922, "7s,As": 0.5821,
            "8c,8d": 0.5715, "8c,8h": 0.5715, "8c,8s": 0.5715,
            "8c,9c": 0.5152, "8c,9d": 0.5013, "8c,9h": 0.5039,
            "8c,9s": 0.5105, "8c,Tc": 0.5299, "8c,Td": 0.5157,
            "8c,Th": 0.517, "8c,Ts": 0.5232, "8c,Jc": 0.5438,
            "8c,Jd": 0.5296, "8c,Jh": 0.5305, "8c,Js": 0.5365,
            "8c,Qc": 0.5567, "8c,Qd": 0.5429, "8c,Qh": 0.5438,
            "8c,Qs": 0.5495, "8c,Kc": 0.5676, "8c,Kd": 0.5558,
            "8c,Kh": 0.5573, "8c,Ks": 0.5618, "8c,Ac": 0.5821,
            "8c,Ad": 0.573, "8c,Ah": 0.5862, "8c,As": 0.5808,
            "8d,8h": 0.5715, "8d,8s": 0.5715, "8d,9c": 0.5105,
            "8d,9d": 0.5013, "8d,9h": 0.5073, "8d,9s": 0.5013,
            "8d,Tc": 0.5232, "8d,Td": 0.5157, "8d,Th": 0.5203,
            "8d,Ts": 0.5157, "8d,Jc": 0.5365, "8d,Jd": 0.5296,
            "8d,Jh": 0.5338, "8d,Js": 0.5296, "8d,Qc": 0.5495,
            "8d,Qd": 0.5429, "8d,Qh": 0.5471, "8d,Qs": 0.5429,
            "8d,Kc": 0.5618, "8d,Kd": 0.5558, "8d,Kh": 0.5606,
            "8d,Ks": 0.5558, "8d,Ac": 0.5808, "8d,Ad": 0.573,
            "8d,Ah": 0.5908, "8d,As": 0.573, "8h,8s": 0.5715,
            "8h,9c": 0.5039, "8h,9d": 0.5073, "8h,9h": 0.5039,
            "8h,9s": 0.5073, "8h,Tc": 0.517, "8h,Td": 0.5203,
            "8h,Th": 0.517, "8h,Ts": 0.5203, "8h,Jc": 0.5305,
            "8h,Jd": 0.5338, "8h,Jh": 0.5305, "8h,Js": 0.5338,
            "8h,Qc": 0.5438, "8h,Qd": 0.5471, "8h,Qh": 0.5438,
            "8h,Qs": 0.5471, "8h,Kc": 0.5573, "8h,Kd": 0.5606,
            "8h,Kh": 0.5573, "8h,Ks": 0.5606, "8h,Ac": 0.5862,
            "8h,Ad": 0.5908, "8h,Ah": 0.5862, "8h,As": 0.5908,
            "8s,9c": 0.5105, "8s,9d": 0.5013, "8s,9h": 0.5073,
            "8s,9s": 0.5105, "8s,Tc": 0.5232, "8s,Td": 0.5157,
            "8s,Th": 0.5203, "8s,Ts": 0.5232, "8s,Jc": 0.5365,
            "8s,Jd": 0.5296, "8s,Jh": 0.5338, "8s,Js": 0.5365,
            "8s,Qc": 0.5495, "8s,Qd": 0.5429, "8s,Qh": 0.5471,
            "8s,Qs": 0.5495, "8s,Kc": 0.5618, "8s,Kd": 0.5558,
            "8s,Kh": 0.5606, "8s,Ks": 0.5618, "8s,Ac": 0.5808,
            "8s,Ad": 0.573, "8s,Ah": 0.5908, "8s,As": 0.5808,
            "9c,9d": 0.5755, "9c,9h": 0.5755, "9c,9s": 0.5755,
            "9c,Tc": 0.5244, "9c,Td": 0.5106, "9c,Th": 0.5122,
            "9c,Ts": 0.5183, "9c,Jc": 0.5399, "9c,Jd": 0.5259,
            "9c,Jh": 0.527, "9c,Js": 0.5329, "9c,Qc": 0.5535,
            "9c,Qd": 0.5399, "9c,Qh": 0.5415, "9c,Qs": 0.5469,
            "9c,Kc": 0.5652, "9c,Kd": 0.5536, "9c,Kh": 0.5552,
            "9c,Ks": 0.5597, "9c,Ac": 0.5809, "9c,Ad": 0.5718,
            "9c,Ah": 0.5851, "9c,As": 0.5797, "9d,9h": 0.5755,
            "9d,9s": 0.5755, "9d,Tc": 0.5183, "9d,Td": 0.5106,
            "9d,Th": 0.5156, "9d,Ts": 0.5106, "9d,Jc": 0.5329,
            "9d,Jd": 0.5259, "9d,Jh": 0.5304, "9d,Js": 0.5259,
            "9d,Qc": 0.5469, "9d,Qd": 0.5399, "9d,Qh": 0.5449,
            "9d,Qs": 0.5399, "9d,Kc": 0.5597, "9d,Kd": 0.5536,
            "9d,Kh": 0.5586, "9d,Ks": 0.5536, "9d,Ac": 0.5797,
            "9d,Ad": 0.5718, "9d,Ah": 0.5897, "9d,As": 0.5718,
            "9h,9s": 0.5755, "9h,Tc": 0.5122, "9h,Td": 0.5156,
            "9h,Th": 0.5122, "9h,Ts": 0.5156, "9h,Jc": 0.527,
            "9h,Jd": 0.5304, "9h,Jh": 0.527, "9h,Js": 0.5304,
            "9h,Qc": 0.5415, "9h,Qd": 0.5449, "9h,Qh": 0.5415,
            "9h,Qs": 0.5449, "9h,Kc": 0.5552, "9h,Kd": 0.5586,
            "9h,Kh": 0.5552, "9h,Ks": 0.5586, "9h,Ac": 0.5851,
            "9h,Ad": 0.5897, "9h,Ah": 0.5851, "9h,As": 0.5897,
            "9s,Tc": 0.5183, "9s,Td": 0.5106, "9s,Th": 0.5156,
            "9s,Ts": 0.5183, "9s,Jc": 0.5329, "9s,Jd": 0.5259,
            "9s,Jh": 0.5304, "9s,Js": 0.5329, "9s,Qc": 0.5469,
            "9s,Qd": 0.5399, "9s,Qh": 0.5449, "9s,Qs": 0.5469,
            "9s,Kc": 0.5597, "9s,Kd": 0.5536, "9s,Kh": 0.5586,
            "9s,Ks": 0.5597, "9s,Ac": 0.5797, "9s,Ad": 0.5718,
            "9s,Ah": 0.5897, "9s,As": 0.5797, "Tc,Td": 0.5818,
            "Tc,Th": 0.5818, "Tc,Ts": 0.5815, "Tc,Jc": 0.5392,
            "Tc,Jd": 0.5267, "Tc,Jh": 0.5283, "Tc,Js": 0.5344,
            "Tc,Qc": 0.554, "Tc,Qd": 0.5415, "Tc,Qh": 0.5434,
            "Tc,Qs": 0.5489, "Tc,Kc": 0.5662, "Tc,Kd": 0.5551,
            "Tc,Kh": 0.5571, "Tc,Ks": 0.5618, "Tc,Ac": 0.5834,
            "Tc,Ad": 0.5744, "Tc,Ah": 0.5885, "Tc,As": 0.5825,
            "Td,Th": 0.5845, "Td,Ts": 0.5842, "Td,Jc": 0.5344,
            "Td,Jd": 0.5267, "Td,Jh": 0.5317, "Td,Js": 0.5267,
            "Td,Qc": 0.5489, "Td,Qd": 0.5415, "Td,Qh": 0.5468,
            "Td,Qs": 0.5415, "Td,Kc": 0.5618, "Td,Kd": 0.5551,
            "Td,Kh": 0.5605, "Td,Ks": 0.5551, "Td,Ac": 0.5825,
            "Td,Ad": 0.5744, "Td,Ah": 0.5931, "Td,As": 0.5744,
            "Th,Ts": 0.5818, "Th,Jc": 0.5283, "Th,Jd": 0.5317,
            "Th,Jh": 0.5283, "Th,Js": 0.5317, "Th,Qc": 0.5434,
            "Th,Qd": 0.5468, "Th,Qh": 0.5434, "Th,Qs": 0.5468,
            "Th,Kc": 0.5571, "Th,Kd": 0.5605, "Th,Kh": 0.5571,
            "Th,Ks": 0.5605, "Th,Ac": 0.5885, "Th,Ad": 0.5931,
            "Th,Ah": 0.5885, "Th,As": 0.5931, "Ts,Jc": 0.5344,
            "Ts,Jd": 0.5267, "Ts,Jh": 0.5317, "Ts,Js": 0.5344,
            "Ts,Qc": 0.5489, "Ts,Qd": 0.5415, "Ts,Qh": 0.5468,
            "Ts,Qs": 0.5489, "Ts,Kc": 0.5618, "Ts,Kd": 0.5551,
            "Ts,Kh": 0.5605, "Ts,Ks": 0.5618, "Ts,Ac": 0.5825,
            "Ts,Ad": 0.5744, "Ts,Ah": 0.5931, "Ts,As": 0.5825,
            "Jc,Jd": 0.6013, "Jc,Jh": 0.6013, "Jc,Js": 0.6013,
            "Jc,Qc": 0.5474, "Jc,Qd": 0.5345, "Jc,Qh": 0.5364,
            "Jc,Qs": 0.542, "Jc,Kc": 0.5737, "Jc,Kd": 0.5631,
            "Jc,Kh": 0.5651, "Jc,Ks": 0.5701, "Jc,Ac": 0.5911,
            "Jc,Ad": 0.5819, "Jc,Ah": 0.5967, "Jc,As": 0.5909,
            "Jd,Jh": 0.6013, "Jd,Js": 0.6013, "Jd,Qc": 0.542,
            "Jd,Qd": 0.5345, "Jd,Qh": 0.5397, "Jd,Qs": 0.5345,
            "Jd,Kc": 0.5701, "Jd,Kd": 0.5631, "Jd,Kh": 0.5685,
            "Jd,Ks": 0.5631, "Jd,Ac": 0.5909, "Jd,Ad": 0.5819,
            "Jd,Ah": 0.6013, "Jd,As": 0.5819, "Jh,Js": 0.6013,
            "Jh,Qc": 0.5364, "Jh,Qd": 0.5397, "Jh,Qh": 0.5364,
            "Jh,Qs": 0.5397, "Jh,Kc": 0.5651, "Jh,Kd": 0.5685,
            "Jh,Kh": 0.5651, "Jh,Ks": 0.5685, "Jh,Ac": 0.5967,
            "Jh,Ad": 0.6013, "Jh,Ah": 0.5967, "Jh,As": 0.6013,
            "Js,Qc": 0.542, "Js,Qd": 0.5345, "Js,Qh": 0.5397,
            "Js,Qs": 0.542, "Js,Kc": 0.5701, "Js,Kd": 0.5631,
            "Js,Kh": 0.5685, "Js,Ks": 0.5701, "Js,Ac": 0.5909,
            "Js,Ad": 0.5819, "Js,Ah": 0.6013, "Js,As": 0.5909,
            "Qc,Qd": 0.6199, "Qc,Qh": 0.6199, "Qc,Qs": 0.6199,
            "Qc,Kc": 0.5813, "Qc,Kd": 0.5711, "Qc,Kh": 0.5731,
            "Qc,Ks": 0.5779, "Qc,Ac": 0.5986, "Qc,Ad": 0.5893,
            "Qc,Ah": 0.6044, "Qc,As": 0.5986, "Qd,Qh": 0.6199,
            "Qd,Qs": 0.6199, "Qd,Kc": 0.5779, "Qd,Kd": 0.5711,
            "Qd,Kh": 0.5765, "Qd,Ks": 0.5711, "Qd,Ac": 0.5986,
            "Qd,Ad": 0.5893, "Qd,Ah": 0.6089, "Qd,As": 0.5893,
            "Qh,Qs": 0.6199, "Qh,Kc": 0.5731, "Qh,Kd": 0.5765,
            "Qh,Kh": 0.5731, "Qh,Ks": 0.5765, "Qh,Ac": 0.6044,
            "Qh,Ad": 0.6089, "Qh,Ah": 0.6044, "Qh,As": 0.6089,
            "Qs,Kc": 0.5779, "Qs,Kd": 0.5711, "Qs,Kh": 0.5765,
            "Qs,Ks": 0.5779, "Qs,Ac": 0.5986, "Qs,Ad": 0.5893,
            "Qs,Ah": 0.6089, "Qs,As": 0.5986, "Kc,Kd": 0.6333,
            "Kc,Kh": 0.6333, "Kc,Ks": 0.6333, "Kc,Ac": 0.6065,
            "Kc,Ad": 0.5973, "Kc,Ah": 0.6124, "Kc,As": 0.6065,
            "Kd,Kh": 0.6333, "Kd,Ks": 0.6333, "Kd,Ac": 0.6065,
            "Kd,Ad": 0.5973, "Kd,Ah": 0.6169, "Kd,As": 0.5973,
            "Kh,Ks": 0.6333, "Kh,Ac": 0.6124, "Kh,Ad": 0.6169,
            "Kh,Ah": 0.6124, "Kh,As": 0.6169, "Ks,Ac": 0.6065,
            "Ks,Ad": 0.5973, "Ks,Ah": 0.6169, "Ks,As": 0.6065,
            "Ac,Ad": 0.6516, "Ac,Ah": 0.6516, "Ac,As": 0.6516,
            "Ad,Ah": 0.6516, "Ad,As": 0.6516, "Ah,As": 0.6516
        }


if __name__ == '__main__':
    run_bot(Player(), parse_args())
