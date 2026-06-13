import eval7
import itertools
import time
import json

def generate_preflop_table():
    deck = eval7.Deck()
    cards = deck.cards

    # 10,000 gives highly accurate equities. 
    # Since this is offline, we can afford the processing time.
    ITERATIONS = 100000
    
    # "XX" is eval7 shorthand for a 100% wide/random range
    villain_range = eval7.HandRange("22+,A2+,K2+,Q2+,J2+,T2+,92+,82+,72+,62+,52+,42+,32+") 
    
    start_time = time.time()
    hands = list(itertools.combinations(cards, 2))
    preflop_equities = {}

    print(f"Total starting hands to calculate: {len(hands)}")
    print(f"Running {ITERATIONS} Monte Carlo iterations per hand...")

    for i, hand in enumerate(hands):
        # 1. Calculate Equity
        equity = eval7.py_hand_vs_range_monte_carlo(list(hand), villain_range, [], ITERATIONS)
        
        # 2. Format the key as a sorted string (e.g., "Ac,Kd")
        # Sorting ensures that ("Ac", "Kd") and ("Kd", "Ac") look exactly the same to the bot
        hand_strs = sorted([str(card) for card in hand])
        dict_key = ",".join(hand_strs)
        
        preflop_equities[dict_key] = round(equity, 4)

        # Print progress every 100 hands so you know it hasn't frozen
        if (i + 1) % 100 == 0:
            print(f"Calculated {i + 1}/{len(hands)} hands...")

    print(f"\nDone! Equities calculated in {round(time.time() - start_time, 2)} seconds.")

    # 3. Export to a JSON file
    output_filename = "preflop_equities.json"
    with open(output_filename, "w") as f:
        json.dump(preflop_equities, f, indent=4)
        
    print(f"Successfully saved lookup table to {output_filename}")

if __name__ == "__main__":
    generate_preflop_table()