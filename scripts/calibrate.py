"""Print both signal scores beside the combined result for every test input.

Run from the repo root:  python -m scripts.calibrate
"""
from scoring import combine
from scripts.test_inputs import TEST_INPUTS
from signals.llm import llm_signal
from signals.stylometry import stylometric_signal


def main():
    print(f"{'input':26} {'words':>5} {'llm':>5} {'stylo':>5} {'combined':>8}  attribution   flags")
    for name, text in TEST_INPUTS.items():
        llm = llm_signal(text)
        stylo = stylometric_signal(text)
        result = combine(llm["score"], stylo["score"], stylo["word_count"])
        llm_str = f"{llm['score']:.2f}" if llm["score"] is not None else "  n/a"
        print(f"{name:26} {stylo['word_count']:>5} {llm_str:>5} {stylo['score']:>5.2f} "
              f"{result['confidence']:>8.3f}  {result['attribution']:12}  {','.join(result['flags'])}")


if __name__ == "__main__":
    main()
