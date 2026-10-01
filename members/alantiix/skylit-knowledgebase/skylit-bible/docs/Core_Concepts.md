# Core Concepts

## ⚙️ Core Concepts

### 🧩 Nodes

**Heatseeker™** is built to reveal **dealer exposure** at each strike price and expiration.\
Each strike displays a **value** (positive or negative), and each value is represented by a **color**:

| Exposure Type         | Color Range    | Typical Behavior                    |
| --------------------- | -------------- | ----------------------------------- |
| **Positive Exposure** | Green → Yellow | Lower-volatility interaction        |
| **Negative Exposure** | Blue → Purple  | Higher-volatility, “wicky” movement |

***

#### 🔑 Key Principle

The most important factor is **not** whether a node is positive or negative — nor its color.\
What truly matters is the **absolute value** of the node.

> 💥 The **larger** the absolute value, the **stronger** the pull it exerts on price.

***

#### ⚡ How Price Interacts with Each Node

* **Positive Node →** Lower-volatility interaction; price tends to move **smoothly**, with fewer wicks or spikes.
* **Negative Node →** Higher-volatility interaction; price becomes **wicky** and more **violent**.\
  When price interacts with a **negative gamma node**, it can **overshoot** before reversing — this is how market makers often **trap retail traders** on the wrong side of the move.

***

### 🧲 Concept of Magnets

Every node on the Heatseeker map acts like a **magnet** in the market.\
Price is **attracted** to these zones due to dealer positioning — yet these same areas can also act as **walls** that repel price and create reversals.

#### 🧠 Magnetic Behavior

* As price **moves farther away** from a high-value node → the **magnetic pull weakens**.
* As price **approaches** a high-value node → the **magnetic pull strengthens**.
* When price **directly interacts** with a node, a **deflection or repulsion** may occur — similar to when two positive ends of a magnet meet and push apart.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FVJRPlBdE2QLxa1rDXGQw%2Fimage.png?alt=media&#x26;token=d7b01328-72c9-4328-8929-3bd546209416" alt=""><figcaption></figcaption></figure>

> 💡 Think of nodes as **dynamic magnets** — their influence grows as price converges and fades as it diverges.

***

### 👑 King Nodes

**King Nodes** are the **highest absolute value nodes** on the heatmap.\
They represent where **Market Makers (MMs)** hold the **greatest exposure** — and where price often **gravitates near expiration**.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FDRwNwvNnCGO0N3Azew55%2Fimage.png?alt=media&#x26;token=37bdfd1b-eaa0-465d-91d7-da1e515eb959" alt="" width="375"><figcaption></figcaption></figure>

#### 🧭 Key Characteristics

* There can be **multiple significant nodes** with large values.
* When multiple strong nodes exist, they can **pull in opposite directions**, creating **range-bound pinning** or **whipsaw movement**.

#### 📈 Price Behavior Around King Nodes

1. **Pin Jobs (Common near End of Day)**\
   MMs often pin price near the King Node late in the session.

   * Tight ranges form.
   * **Scalping the range edges** tends to work best.

   <figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2F0yvDvA0sSx2Cwoa8D853%2Fimage.png?alt=media&#x26;token=6da52c52-ccd6-4f16-be68-ea165fa63c1f" alt=""><figcaption></figcaption></figure>
2. **Drives Away (Common early in the day)**\
   When price reaches the King Node **too early**, MMs may push it away.\
   Holding price there all session would require constant defense, so they often trigger an early **drive-off**.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FaU9hhCPCawXa2Ol4T9GK%2Fimage.png?alt=media&#x26;token=68a260e5-98b1-4c40-8bc0-3005c3193f1d" alt=""><figcaption></figcaption></figure>

#### ⚠️ Margin of Interaction

King Nodes don’t always reject **to the exact cent**.\
Expect a **deflection margin** of roughly **5–10 points on SPX**.&#x20;

> Note: A rejection occurring 5½ points away from the King Node is still considered valid, as noted in the example above.

***

### 🚪 Gatekeeper Nodes

**Gatekeeper Nodes** act like **bouncers at the door of a nightclub** — they prevent price from easily reaching the King Node.

These nodes function as **deflection points** that can cause major directional shifts.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FfqHJFI9uTU0LgUey9UfC%2Fimage.png?alt=media&#x26;token=20be6cc5-fdd4-4ef7-8118-34553249b29d" alt=""><figcaption></figcaption></figure>

#### 🔍 Behavior

* If price **tests and fails** at a Gatekeeper Node → the map can **reshuffle**.
* **Reshuffles** often precede a **trend change** or **realignment** of where dealers aim to pin price.
* Gatekeeper rejections near the **start of the day** often mark **high-probability reversals**.

> ⚡ After rejection, reshuffles occur — and clarity emerges on the next directional bias.

***

### ⚖️ Midpoints

**Midpoints** are Market Makers’ favorite **trap zones** — and they offer the **worst risk-to-reward** for directional traders.

* Unless you’re an **option or spread seller**, avoid trading them.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FAKGQuuLjEFSbgw5t8eRr%2Fimage.png?alt=media&#x26;token=b3f53aa2-84b6-41bf-a7b4-198eff468389" alt=""><figcaption></figcaption></figure>

#### 📊 Why Midpoints Are Dangerous

* Market Dealers love **range-bound** conditions.
* The **upper and lower ranges** are easily visible on the heatmap.
* When price is **in the middle of the range**, direction becomes **uncertain**.

At best, your **risk-to-reward** is roughly **1:1** — not asymmetric and not worth taking.

> ✅ **B*****etter Strategy: Wait for price to approach the edge of the range and fade the edge.***\
> ***Fading extremes offers the best R:R and highest probability.***

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2Ft6z1pbNJAeQB0bAtZwFl%2Fimage.png?alt=media&#x26;token=c59de3ec-cc1e-4070-8d40-6ed7208e9e80" alt=""><figcaption></figcaption></figure>

***

### 🔁 Price Delivery & Node Retests

When looking for **bounce plays** off a node, **context matters** — not all nodes retain influence forever.

#### 🧮 Node Retest Strength

| Touch              | Reaction Strength | Probability | Notes                                    |
| ------------------ | ----------------- | ----------- | ---------------------------------------- |
| **First Touch**    | Strongest         | Highest     | Fresh liquidity; MMs defend aggressively |
| **Second Touch**   | Moderate          | \~66%       | Often forms double tops/bottoms          |
| **Third+ Touches** | Weakest           | \~33%       | Reduced probability of reversal          |

If price has already been **delivered from** a node (touched and moved away), its **influence weakens**.\
Each additional test reduces the likelihood of a strong bounce.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2Fg6ReOCLmhrwimEuH1Mvo%2Fimage.png?alt=media&#x26;token=83c54cf8-de7e-45ab-9f2b-83f2e84a88c7" alt=""><figcaption></figcaption></figure>

* *Caption: ACN 240 node test, targeting upside nodes, from September 10th, 2025.*

> 💡 **Key Takeaway:**\
> Prioritize **untouched nodes** for bounce plays — treat previously tested ones with caution.

***

### :e-mail: Price Delivery From a Node

When price bounces off a Gatekeeper node or King node, we don't always get reversion back to that specific node!&#x20;

* A node that has been interacted with tends to have less influence over price action, because the node is no longer "fresh".&#x20;
* The probability of reversion back to that node becomes lesser, making conditions unfavorable for a green trade.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2F4ZiDcWbJqrGA4bIHRLSC%2Fimage.png?alt=media&#x26;token=224b4c2d-243d-4ae7-9c79-f1cae54c907d" alt=""><figcaption></figcaption></figure>

***Key note: Instead of trying to chase a node that has already been interacted with, it is better to move on to the next trade. Looking for the freshest levels possible provides the highest probability for success.***

### Price Delivery and Actionable Trades

* If price gets a rejection (in a bearish scenario) or a bounce (in a bullish scenario) off a node, a few things may occur:

  * Gradual decrease of the node we were delivered from - in this case, the likelihood of a return back to the node in question becomes low probability.

  &#x20;                   *PEP Price Delivery Case Study From October 15th, 2025 at 09:30am EST*

  <figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FDIsMcgemvZB9tlDBQPNX%2Fimage.png?alt=media&#x26;token=66f1e9dc-cfdc-44c9-9a2a-c42ea322f376" alt=""><figcaption></figcaption></figure>

  * Increase of the node we were delivered from - higher likelihood of a reversion back to the node in question.

  *By keeping an eye on key nodes for rate of change, we can make inferences as to whether a reversion back to a deflection node is likely. Looking for increase in size of node gives us higher likelihood for price to return, whereas a decrease in node size gives us lower likelihood for reversion.*

***

### ⚡ Robinhood Power Hour Liquidation

At **3:30 PM EST** (30 minutes before market close), Robinhood begins **auto-liquidating** accounts that fail margin requirements.\
This creates **forced order flow** in highly liquid names like **SPX, SPY, and QQQ**.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FHpFuS5ScvWNSlnb5AdfK%2Fimage.png?alt=media&#x26;token=6a88c55a-92af-4185-bd94-8a669114c1a0" alt=""><figcaption></figcaption></figure>

#### 💥 Why It Matters

* Triggers **volatility spikes** right before close.
* Can **force breakouts or fakeouts** near Gatekeeper Nodes.
* Near a **King Node**, can **accelerate** the move.
* Sometimes reshuffles the entire map.

> ⚠️ Always stay alert to **Power Hour volatility** — it’s often broker-driven and mechanical, not organic.

***

### 📈 Rate of Change of a Node

Heatseeker™ not only shows **dealer positioning**, but also **how fast liquidity changes**.

#### 🔄 Reading Node Momentum

* **Rapid Accumulation →** Dealers are quickly adding exposure; acts like a **magnet** that pulls price in strongly.
* **Rapid Unwinding →** Exposure vanishes; levels that looked strong may suddenly **weaken**.
* Fast changes often cause **volatility spikes**, **explosive moves**, or **sharp reversals**.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FwC8TVxiylpiA7XN59QUI%2Fimage.png?alt=media&#x26;token=a4b837e0-d0e2-47b6-a003-1ffc21c2eb44" alt=""><figcaption><p>SPY 660 bounce from October 13th, 2025. QQQ had a strong floor that wasnt supporting lower, SPX was showing upside accumulation. <em>Due to the fact that QQQ did not support lower and SPX began growing to the upside, we had 2/3 confluence for longs. The rapid unwinding of SPY downside nodes led to a a sharp, V-shaped recovery, emphasizing the important of watching nodes for rate of change.</em></p></figcaption></figure>

> 🧠 Watch the **rate of change** — it reveals urgency and intent behind Market Maker adjustments.

***

### 🛡️ Hedge Nodes

**Hedge Nodes** appear during **major news or macro events** — such as FOMC, CPI, JOLTS, NFP, or earnings.\
They represent **large, protective positions** that sit **farther from price** and move **slowly** throughout the day.

<figure><img src="https://2531159329-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2Fud36kD3HdHbBoBHgpCc8%2Fuploads%2FdLwgm7MqYNuHqHdzX1xH%2Fimage.png?alt=media&#x26;token=ddba9021-7911-470e-97fb-8423b1656589" alt=""><figcaption></figcaption></figure>

#### Characteristics

* Typically **static or slow-unwinding**.
* Can appear **above and below price** simultaneously, typically far away from current spot price.
* Function like **insurance** rather than active magnets.

The **closer** a Hedge Node is to current price, the **more it shapes intraday behavior**.\
The **farther** it is, the **less influence** it exerts.

> 💡 Watch for **gradual unwinds** of large hedge nodes — they often signal changing Market Maker expectations.

***

### 🧨 OPEX Nodes

Every **third Friday** of the month marks **OPEX Week** — the monthly options expiration cycle.

#### What Happens During OPEX

* Nodes may carry **less weight**, since many contracts are set to **expire**.
* Positions roll off or reset, leading to **temporary distortions** in dealer positioning.
* After OPEX week passes, **directional bias** and **map clarity** typically **improve immediately**.

> ⚠️ Treat OPEX week levels with caution — probabilities are temporarily skewed.

***

✅ **In Summary:**

* Focus on **absolute value** — not color or sign.
* Favor **untouched nodes** over retested ones.
* Watch **rate of change** for clues on momentum.
* Avoid **midpoints**, **respect gatekeepers**, and **track king nodes** daily.
* Be mindful of **external catalysts** like **Power Hour**, **OPEX**, and **Hedge Nodes**.
