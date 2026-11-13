# Graph Report - nob-api  (2026-11-14)

## Corpus Check
- 15 files · ~48,584 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: (none) 2, .example 1, .woff2 1)

## Summary
- 2127 nodes · 4909 edges · 107 communities (68 shown, 39 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 337 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- pnl.py
- PnL Analytics & Risk Radar
- Nobitex Client & Background Sync
- db.py
- Authentication & Ed25519 Cryptography
- $
- chart.umd.min.js
- js
- Margin Positions & Leveraged Orders
- Journal API & Data Validation
- get_market_prices
- iw
- Internationalization & Jalali Dates
- Nobitex API Package Spec
- 🌟 ویژگی‌های کلیدی
- w
- Jt
- K
- add
- Si
- manifest.json
- decl
- J
- o
- re
- nt
- tn
- Pl
- T
- e
- _n
- oo
- sn
- Na
- s
- .get
- na
- sJ
- .update
- Ro
- Ho
- normalize
- stringify
- prefixed
- El
- Ss
- bo
- ._computeLabelItems
- .notifyPlugins
- Vg
- .draw
- .buildOrUpdateControllers
- ul
- As
- xt
- _deleteIfExpired
- N
- u
- hs
- Rn
- jalaali.min.js
- v2
- eS
- update
- .getPixelForDecimal
- em
- sort
- error
- qr
- .getLabels
- create
- Pa
- api_delete_journal
- FC
- ja
- bo
- cm
- each
- yk
- gR
- jC
- ro
- Ak
- ao
- Ba
- bk
- ca
- Ia
- Za
- Io
- getProxyProcessor
- Gy
- hk
- Ma
- Wa
- nS
- OC
- prefixes
- qa
- showSourceCode
- Vn
- WC
- zk
- cleanRaws
- ei

## God Nodes (most connected - your core abstractions)
1. `$` - 485 edges
2. `tn` - 131 edges
3. `js` - 58 edges
4. `iw()` - 53 edges
5. `e()` - 50 edges
6. `n()` - 50 edges
7. `s()` - 44 edges
8. `w()` - 35 edges
9. `_n` - 34 edges
10. `T()` - 34 edges

## Surprising Connections (you probably didn't know these)
- `۱. کلون و نصب پیش‌نیازها` --references--> `uv()`  [INFERRED]
  README.fa.md → src/nob/static/vendor/vue.esm-browser.prod.js
- `Position Size & Risk Calculator` --references--> `api_get_markets()`  [INFERRED]
  README.md → src/nob/server.py
- `Liquidation Risk Radar` --references--> `calculate_positions_summary()`  [INFERRED]
  README.md → src/nob/pnl.py
- `Daily PnL Calendar` --references--> `api_get_pnl_calendar()`  [EXTRACTED]
  README.md → src/nob/server.py
- `Equity Curve & Cumulative PnL` --references--> `api_get_equity_curve()`  [EXTRACTED]
  README.md → src/nob/server.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Dashboard Tab Architecture** — src_nob_static_index_html_tab_spot, src_nob_static_index_html_tab_futures, src_nob_static_index_html_tab_calendar, src_nob_static_index_html_tab_trades [INFERRED 0.85]

## Communities (107 total, 39 thin omitted)

### Community 0 - "pnl.py"
Cohesion: 0.23
Nodes (5): Liquidation Risk Radar, calculate_daily_pnl_calendar(), calculate_equity_curve(), calculate_portfolio(), calculate_positions_summary()

### Community 1 - "PnL Analytics & Risk Radar"
Cohesion: 0.08
Nodes (13): Trading Journal & Strategy Playbook, api_export_csv(), api_get_journal(), api_get_portfolio(), api_get_portfolio_charts(), api_get_stats(), api_get_trades(), api_sync_status() (+5 more)

### Community 2 - "Nobitex Client & Background Sync"
Cohesion: 0.15
Nodes (5): NobitexClient, sign_request(), api_refresh_prices(), api_sync_trades(), perform_sync()

### Community 3 - "db.py"
Cohesion: 0.12
Nodes (19): delete_journal_entry(), get_all_crypto_trades_chronological(), get_all_journal_entries(), get_btc_daily_history(), get_db_connection(), get_distinct_markets(), get_journal_entry(), get_journal_tags_summary() (+11 more)

### Community 4 - "Authentication & Ed25519 Cryptography"
Cohesion: 0.11
Nodes (4): export_trades_csv(), find_env_file(), load_credentials(), load_dotenv()

### Community 5 - "$"
Cohesion: 0.01
Nodes (187): $, a3, aa, ac, ad, ae, aG, ai (+179 more)

### Community 7 - "chart.umd.min.js"
Cohesion: 0.04
Nodes (41): ai(), at(), beforeLayout(), Bt(), buildLookupTable(), dataset(), ea(), Ft() (+33 more)

### Community 8 - "js"
Cohesion: 0.05
Nodes (16): Bn(), _calculateBarValuePixels(), getLabelAndValue(), getMaxOverflow(), jn(), js, g(), Ls() (+8 more)

### Community 9 - "Margin Positions & Leveraged Orders"
Cohesion: 0.33
Nodes (3): upsert_positions(), api_get_positions(), Margin & Leveraged Positions Tab

### Community 10 - "Journal API & Data Validation"
Cohesion: 0.40
Nodes (3): api_save_journal(), JournalPayload, Trading Journal & Notes Modal

### Community 11 - "get_market_prices"
Cohesion: 0.13
Nodes (10): Equity Curve & Cumulative PnL, Daily PnL Calendar, Position Size & Risk Calculator, get_market_prices(), api_get_equity_curve(), api_get_markets(), api_get_pnl_calendar(), sync_btc_history_if_needed() (+2 more)

### Community 12 - "iw"
Cohesion: 0.06
Nodes (68): A(), b(), B, C(), E(), ea(), ec(), ed() (+60 more)

### Community 16 - "🌟 ویژگی‌های کلیدی"
Cohesion: 0.11
Nodes (18): 🔒 امنیت و حریم خصوصی, داشبورد مدیریت پورتفولیو و معاملات نوبیتکس 📊, 🚀 راهنمای نصب و اجرا, 📄 مجوز انتشار (License), 📖 معرفی پروژه, 🌟 ویژگی‌های کلیدی, پیش‌نیازها, ۱. 📊 ارزش‌گذاری دارایی‌ها و تحلیل پورتفولیو (+10 more)

### Community 17 - "w"
Cohesion: 0.07
Nodes (66): a1(), a2(), a6(), ab(), af(), ag(), ah(), aI() (+58 more)

### Community 18 - "Jt"
Cohesion: 0.06
Nodes (15): ce(), de, en, he(), It(), jt(), Jt, kt() (+7 more)

### Community 19 - "K"
Cohesion: 0.06
Nodes (46): _a(), Ae(), ax(), Bf(), ea(), en(), ex(), Ff() (+38 more)

### Community 20 - "add"
Cohesion: 0.05
Nodes (46): add(), already(), au(), calcBefore(), check(), checkForWarning(), clean(), cleanBrackets() (+38 more)

### Community 21 - "Si"
Cohesion: 0.09
Nodes (18): draw(), Ee(), fo(), generateLabels(), ho(), ki(), Le(), lo() (+10 more)

### Community 22 - "manifest.json"
Cohesion: 0.05
Nodes (38): 1k_shib, aave, ada, baba, bnb, btc, cake, copxon (+30 more)

### Community 23 - "decl"
Cohesion: 0.08
Nodes (39): atrule(), beforeAfter(), block(), body(), checkMissedSemicolon(), Co(), colon(), comment() (+31 more)

### Community 24 - "J"
Cohesion: 0.07
Nodes (34): Bh(), cr(), dr(), i(), Eh(), I_(), info(), J() (+26 more)

### Community 25 - "o"
Cohesion: 0.09
Nodes (18): a(), aa(), afterDatasetsUpdate(), configure(), determineDataLimits(), Di(), g(), Gs() (+10 more)

### Community 26 - "re"
Cohesion: 0.08
Nodes (35): rn(), f(), g, h, i3(), i4(), id(), ig() (+27 more)

### Community 27 - "nt"
Cohesion: 0.11
Nodes (27): e0(), e1(), e2(), e4(), e5, eJ(), ek(), eq() (+19 more)

### Community 29 - "Pl"
Cohesion: 0.07
Nodes (33): _2(), Ah(), as(), B(), c(), $d(), E(), E2() (+25 more)

### Community 30 - "T"
Cohesion: 0.08
Nodes (29): i2(), iu(), l_(), lm(), lu(), M(), lz(), n0() (+21 more)

### Community 31 - "e"
Cohesion: 0.09
Nodes (30): ao(), co(), dn(), Do(), e(), ei(), fe(), gi() (+22 more)

### Community 32 - "_n"
Cohesion: 0.10
Nodes (4): fn(), gn(), _n, xn()

### Community 33 - "oo"
Cohesion: 0.08
Nodes (18): beforeDatasetDraw(), beforeDatasetsDraw(), beforeDraw(), ca, da(), et(), fa(), ga() (+10 more)

### Community 34 - "sn"
Cohesion: 0.12
Nodes (3): an(), on, sn

### Community 35 - "Na"
Cohesion: 0.14
Nodes (8): afterInit(), Ba(), Ca(), f(), initialize(), Na, Ta(), za()

### Community 36 - "s"
Cohesion: 0.10
Nodes (26): _calculateBarIndexPixels(), De(), _getAxis(), _getAxisCount(), getFirstScaleIdForIndexAxis(), getPixelForTick(), getPixelForValue(), _getRuler() (+18 more)

### Community 37 - ".get"
Cohesion: 0.08
Nodes (13): e6, e8, e9, e$, eV, lj(), tD, tg() (+5 more)

### Community 38 - "na"
Cohesion: 0.09
Nodes (27): cx(), dm(), ec(), eE(), F_(), Fh(), fS(), fx() (+19 more)

### Community 40 - ".update"
Cohesion: 0.13
Nodes (4): d(), dt(), Pn(), Sn()

### Community 41 - "Ro"
Cohesion: 0.13
Nodes (6): Bi(), Ci(), Fi(), Ro, zi(), preprocess()

### Community 42 - "Ho"
Cohesion: 0.14
Nodes (6): Bo(), _generate(), _getTimestampsForTable(), Ho, nt(), Vo()

### Community 43 - "normalize"
Cohesion: 0.10
Nodes (24): append(), Cc(), constructor(), createTokenizer(), group(), index(), insertAfter(), insertBefore() (+16 more)

### Community 44 - "stringify"
Cohesion: 0.13
Nodes (23): async(), catch(), content(), css(), div(), dp(), finally(), getAsyncError() (+15 more)

### Community 45 - "prefixed"
Cohesion: 0.11
Nodes (22): cloneDiv(), colorStops(), convertDirection(), fixAngle(), fixDirection(), fixRadial(), isRadial(), isStretch() (+14 more)

### Community 46 - "El"
Cohesion: 0.10
Nodes (21): c2(), cs(), El(), f2(), fy(), gg(), h_(), il() (+13 more)

### Community 47 - "Ss"
Cohesion: 0.13
Nodes (13): Be(), bs(), ct(), Fs(), ge(), ms(), Ss, vs() (+5 more)

### Community 48 - "bo"
Cohesion: 0.12
Nodes (7): bo, H(), j(), ne(), numeric(), ro(), xo()

### Community 51 - "Vg"
Cohesion: 0.12
Nodes (18): cy(), Dl(), dy(), Fg(), G2(), Gl(), jg(), L2() (+10 more)

### Community 52 - ".draw"
Cohesion: 0.20
Nodes (3): Ie(), Qs(), ze()

### Community 54 - "ul"
Cohesion: 0.15
Nodes (15): br(), d2(), dx(), h2(), I2(), kt(), L(), ly() (+7 more)

### Community 57 - "_deleteIfExpired"
Cohesion: 0.16
Nodes (14): delete(), _deleteIfExpired(), _emitEvictions(), _entriesAscending(), entriesDescending(), get(), _getItemValue(), _getOrDeleteIfExpired() (+6 more)

### Community 58 - "N"
Cohesion: 0.16
Nodes (14): ef(), eg(), eh(), ep(), N(), sb(), sc(), sd() (+6 more)

### Community 59 - "u"
Cohesion: 0.21
Nodes (8): b(), cn(), eo(), hn(), ln(), u(), un(), wn()

### Community 62 - "jalaali.min.js"
Cohesion: 0.52
Nodes (11): a(), c(), d(), f(), g(), i(), l(), o() (+3 more)

### Community 63 - "v2"
Cohesion: 0.17
Nodes (12): aa(), ac(), b2(), bx(), Cl(), ol(), Sg(), sn() (+4 more)

### Community 64 - "eS"
Cohesion: 0.26
Nodes (3): eN(), eR(), eS

### Community 65 - "update"
Cohesion: 0.20
Nodes (10): addElements(), as(), es(), is(), ns(), os(), rs(), ss() (+2 more)

### Community 68 - "sort"
Cohesion: 0.22
Nodes (9): applyVariantOffset(), compare(), Lh(), qh(), recalculateVariantOffsets(), remapArbitraryVariantOffsets(), select(), sort() (+1 more)

### Community 69 - "error"
Cohesion: 0.38
Nodes (7): error(), fromOffset(), origin(), positionBy(), positionInside(), rangeBy(), Wr()

### Community 70 - "qr"
Cohesion: 0.33
Nodes (6): blueGray(), coolGray(), lightBlue(), qr(), trueGray(), warmGray()

### Community 71 - ".getLabels"
Cohesion: 0.40
Nodes (3): buildTicks(), init(), mo()

### Community 72 - "create"
Cohesion: 0.40
Nodes (5): arbitraryProperty(), create(), forVariant(), recordVariant(), recordVariants()

### Community 73 - "Pa"
Cohesion: 0.50
Nodes (3): OA(), Pa(), wk()

### Community 75 - "FC"
Cohesion: 0.67
Nodes (3): BC(), FC(), RootExit()

### Community 76 - "ja"
Cohesion: 0.67
Nodes (3): bd(), ja(), Jk()

### Community 77 - "bo"
Cohesion: 0.67
Nodes (3): bo(), TC(), Ue()

### Community 78 - "cm"
Cohesion: 0.67
Nodes (3): cm(), mm(), rE()

### Community 79 - "each"
Cohesion: 0.67
Nodes (3): each(), getIterator(), walk()

### Community 80 - "yk"
Cohesion: 0.67
Nodes (3): gk(), Np(), yk()

### Community 81 - "gR"
Cohesion: 1.00
Nodes (3): gR(), M0(), Yu()

### Community 82 - "jC"
Cohesion: 0.67
Nodes (3): jC(), NC(), Once()

### Community 83 - "ro"
Cohesion: 0.67
Nodes (3): LS(), ro(), To()

## Knowledge Gaps
- **13 isolated node(s):** `nob-api`, `Liquidation Risk Radar`, `Daily PnL Calendar`, `Position Size & Risk Calculator Modal`, `Interactive PnL Calendar Tab` (+8 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 569 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **39 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `$` connect `$` to `eS`, `nS`, `em`, `re`, `.get`, `na`, `sJ`, `iw`, `w`, `K`, `J`, `N`, `nt`, `T`, `e`?**
  _High betweenness centrality (0.315) - this node is a cross-community bridge._
- **Are the 101 inferred relationships involving `$` (e.g. with `Y()` and `J()`) actually correct?**
  _`$` has 101 INFERRED edges - model-reasoned connections that need verification._
- **What connects `nob-api`, `Liquidation Risk Radar`, `Daily PnL Calendar` to the rest of the system?**
  _13 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `PnL Analytics & Risk Radar` be split into smaller, more focused modules?**
  _Cohesion score 0.07862903225806452 - nodes in this community are weakly interconnected._
- **Why does `n()` connect `e` to `tailwind.js`, `chart.umd.min.js`, `js`, `iw`, `w`, `add`, `Si`, `o`, `tn`, `Pl`, `T`, `_n`, `oo`, `Na`, `s`, `.get`, `Ho`, `Ss`, `._computeLabelItems`, `.draw`, `.buildOrUpdateControllers`, `As`, `u`, `FC`, `jC`?**
  _High betweenness centrality (0.163) - this node is a cross-community bridge._
- **Are the 38 inferred relationships involving `e()` (e.g. with `b()` and `co()`) actually correct?**
  _`e()` has 38 INFERRED edges - model-reasoned connections that need verification._
- **Should `db.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12050739957716702 - nodes in this community are weakly interconnected._