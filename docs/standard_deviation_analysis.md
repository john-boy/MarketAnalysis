# Standard Deviation Analysis

# John

I was wandering around in the code looking for the RSI functionality.  I found two relevant functions; relative_rsi_panel and rsi_panel.  The rsi_panel seems to be the code responsible for the RSI display. What is the purpose for the relatvie_rsi_panel?  My curiosity is based on the anticipated need to calculate a relative RSI using SPX data as the common baseline.  Once calculated the relative RSI value will be used to compute a standard deviation so we have a well-founded computational value to compare across many symbols.  Your thoughts?

# Claude

## Purpose of `relative_rsi_panel`

It's exactly the primitive for your use case. Given a `(T,N)` panel of constituent closes and a `(T,)` index close vector on the same date axis, it returns `(T,N)` of `constituent_rsi − index_rsi`. Positive = constituent is stronger than the index on momentum that day; NaN where either side isn't seeded. Built on the GPU-capable `xp` backend in [indicators_cross.py:24](market_analysis/services/indicators_cross.py:24), so it scales to the full universe.

There's already a companion loader — [load_constituents_vs_index_panel](market_analysis/services/indicators_cross.py:119) — that aligns SPX + constituents onto a union date axis and hands back `(index_closes, constituent_panel)` ready to feed the function. Since SPX now lands in `daily_quotes` directly (post-2.5), passing `"SPX"` to that loader will just work.

## Fit for your plan

Your pipeline would be:
1. `index_closes, panel = load_constituents_vs_index_panel("SPX", symbols)`
2. `rel = relative_rsi_panel(panel.closes, index_closes, period=14)` → `(T,N)` relative RSI
3. Then the std-dev step — **this is where you have a design choice**:
   - **Rolling σ along T** (per symbol, trailing window): a measure of how stable each symbol's momentum *gap vs SPX* is over time. Good for regime-change detection; each symbol is judged against its own history.
   - **Cross-sectional σ across N at each t**: dispersion across the universe on each date. Each symbol's z-score = (its relative RSI − universe mean) / universe σ at that t. Good for "who is meaningfully stretched versus peers right now?"

My recommendation: both are ~10-line additions sitting next to the existing cross-sectional code. Start with the cross-sectional z-score since it pairs naturally with `cross_rank_panel` already there and gives directly comparable values across symbols. The rolling per-symbol σ is worth adding as a second pass when you want to filter for "meaningful" vs noisy relative-RSI readings.

One caveat: `relative_rsi_panel` returns the raw difference, not a scale-normalized measure — RSI is already bounded in `[0,100]` so a 5-point gap means something, but std-dev normalization is still the right move for cross-symbol comparability, and you've identified that correctly.
