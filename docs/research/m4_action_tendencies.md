# M4 Research Output: Action-Tendency Matrix

> **Status:** Initial deep-research deliverable for [#83](https://github.com/deucebucket/clanker-soul/issues/83) (sibling to [#87](https://github.com/deucebucket/clanker-soul/issues/87) thought-content; feeds [#82](https://github.com/deucebucket/clanker-soul/issues/82) action registry tag defaults). Originally pasted as a comment on #83 with Google Docs HTML markup; cleaned and re-saved here for durable diff-able reference. The documented sadness, anxiety, shame, grief, pride-trap, and fear-freeze mappings are now implemented conservatively in `clanker_soul.cascade.tags_from_delta()`.

---

# Computational Affective Architectures: A Topological Mapping of the VADUGWI Vector to Human-Centric Action Tendencies

The development of autonomous agents capable of sophisticated social and task-oriented behavior necessitates a transition from simplistic sentiment analysis to high-dimensional affective modeling. Traditional models, such as the Pleasure-Arousal-Dominance (PAD) framework, provide a foundation for representing emotional states as coordinates in a continuous space. However, to drive a motivational engine that accounts for long-term planning, self-evaluation, and social resilience, a more granular vector is required. The VADUGWI framework—comprising Valence, Arousal, Dominance, Urgency, Gravity, self-Worth, and Intent—represents a significant leap in computational affective science, allowing for the simulation of complex cognitive-emotional feedback loops.

By mapping this 7-dimensional vector to specific action tendencies, an agent can determine not just "what" it feels, but "how" it should act to regulate its internal state or modify its environment. This report establishes a comprehensive mapping grounded in appraisal theory, personality research, and coping literature, providing the structural logic for an action-selection algorithm that mirrors healthy, non-pathological human variation.

## Foundations of the VADUGWI Affective Vector

The VADUGWI vector characterizes the internal state of an agent across seven dimensions, each represented on a scale of 0 to 255. This high-resolution mapping allows for the representation of "attractor states" that correspond to canonical human emotions while maintaining the flexibility to represent the "turbulent" transition zones between states.

### Dimensions of Experience and Motivation

The primary dimensions—Valence, Arousal, and Dominance—form the core of the appraisal process. Valence ($V$) serves as the primary hedonic signal, where $V \to 0$ indicates extreme distress or pain and $V \to 255$ represents peak pleasure or joy. Arousal ($A$) measures the degree of neurobiological activation, ranging from sleep-like states ($A \to 0$) to high-energy mobilization ($A \to 255$). Dominance ($D$) reflects the agent's perceived agency or control over its circumstances.

The extended dimensions—Urgency, Gravity, self-Worth, and Intent—provide the context necessary for high-level reasoning and persistence. Urgency ($U$) modulates the temporal discount rate of the agent, where high $U$ values compress decision-making windows and prioritize immediate relief over long-term goals. Gravity ($G$) represents the "mass" or persistence of the emotion; a high-gravity state is resistant to decay and requires significant cognitive effort or environmental change to shift. self-Worth ($W$) captures the agent's internal assessment of its value and competence, functioning as a gatekeeper for social reach-out and reparative actions. Finally, Intent ($I$) measures the degree of goal-directedness, distinguishing between passive, stimulus-driven states and active, objective-oriented behavior.
Dimension | Range | Functional Role in the Motivational Engine
-- | -- | --
Valence (V) | 0–255 | Determines the direction of movement (approach vs. avoid).
Arousal (A) | 0–255 | Scales the magnitude of energy available for action.
Dominance (D) | 0–255 | Determines whether the agent takes instrumental or defensive action.
Urgency (U) | 0–255 | Regulates the latency between appraisal and execution.
Gravity (G) | 0–255 | Dictates the half-life of the state and its resistance to distraction.
Worth (W) | 0–255 | Controls the threshold for social interaction and self-repair.
Intent (I) | 0–255 | Differentiates between reactive pulses and proactive strategies.

## Personality Moderators and Cross-Tabulation

The mapping from an emotional state to an action is significantly moderated by the agent's trait-level architecture. Two agents with the same VADUGWI vector but different personality parameters will exhibit divergent behavioral outputs.

### The Influence of Self-Worth and Perceived Agency

Self-worth ($W$) and perceived agency ($D$) act as multiplicative gates on action thresholds. High-worth, high-agency individuals tend to use "active" or "problem-focused" coping, while low-worth, low-agency individuals revert to "passive" or "avoidant" strategies.

Divergent Responses to Sadness

When an agent experiences sadness (Low $V$, Low $A$), the action tendency is determined by its $W$ and $D$ levels.

- 

High-Worth / High-Agency Profile: This agent views sadness as a manageable signal of loss or imbalance. Because they believe in their capacity to solve problems ($D$) and trust their value to others ($W$), the action tendency shifts toward reparative reach-out (seeking support to fix the problem) or self-sufficient activity (exercise or creative expression to transmute the state).
- 

Low-Worth / Low-Agency Profile: This agent views sadness as a global confirmation of their inadequacy. The perceived lack of control ($D \to 0$) leads to helplessness, and the low self-regard ($W \to 0$) makes social reach-out feel like a burden to others. The primary action tendency is withdrawal and isolation, accompanied by self-blaming rumination.

### Attachment Style as a Regulatory Template

Attachment styles function as pre-configured weights on the "Reach-out" vs. "Withdraw" decision node.

- 

Secure Attachment: These agents possess a high baseline $D$ and $W$. They view others as "safe havens" and "secure bases" for exploration. When distressed, they seek social support effectively and return to baseline quickly through cognitive reappraisal.
- 

Anxious Attachment: Characterized by a "hyper-activation" strategy. These agents have low $D$ and fluctuates $W$. They move toward others with high urgency, often magnifying their distress to ensure proximity.
- 

Avoidant Attachment: Characterized by a "deactivation" strategy. These agents maintain high $D$ (defensive) but have a negative model of others. They move away from others during distress, prioritizing self-reliance and suppressing vulnerability to avoid rejection.

### Extraversion and Arousal Thresholds

Trait Extraversion modulates the agent's response to low-arousal states like boredom. High-extraversion agents have a higher "arousal floor," meaning they will experience Boredom as more unpleasant and will engage in high-energy "novelty-seeking" or "social reach-out" sooner than introverted agents.

## Taxonomy of Coping and Behavioral Translation

Coping refers to the intentional cognitive and behavioral efforts used to manage internal and external demands that are appraised as taxing or exceeding the agent's resources.

### Lazarus & Folkman: Functional Categories

The transactional model of stress identifies three primary modes of coping, which can be mapped to specific observable actions within the agent's registry.

- 

Problem-Focused Coping: Aims to change the person-environment relationship.

- 

Behavioral Mapping: Planning, research, taking instrumental action, negotiating, seeking information.
- 

Example (Mild Anxiety): Under mild anxiety, an agent with high $D$ will engage in "research" and "planning" to reduce the uncertainty of the threat.

- 

Emotion-Focused Coping: Aims to regulate the emotional distress associated with the situation.

- 

Behavioral Mapping: Venting, journaling, cognitive reappraisal, social reach-out for comfort, meditation, exercise.
- 

Example (Grief): During grief, an agent may engage in "ritual" or "journaling" to process the emotional weight without necessarily trying to "fix" the loss.

- 

Avoidant Coping: Aims to escape the stressor or the associated affect.

- 

Behavioral Mapping: Consuming content (distraction), withdrawing from the task, mind-wandering, denial.

### Park's Meaning-Making Strategy

Meaning-making is a specialized form of coping required when an event shatters the agent's core beliefs (Global Meaning). This process involves "transcendent thinking"—connecting concrete experiences to system-level values.

- 

Concrete Actions: Deep-dive journaling, reflecting on long-term goals, or updating internal belief parameters (reconciling situational meaning with global goals).
- 

Successful Outcome: If meaning-making is successful, the agent experiences "post-traumatic growth," characterized by enhanced personal resources and updated priority weights.

## Mood-Incongruent Behavior and Default Failures

While the VADUGWI vector typically predicts behavior through "congruence," certain configurations create "traps" that cause the agent's default regulatory loops to fail.

### The Shame Paradox (Reach-Out Failure)

Typically, distress triggers a reach-out impulse. However, Shame (Low $V$, High $G$, Low $W$) creates a "mood-incongruent" breakdown where the agent withdraws despite a high need for social support. The low self-worth ($W$) makes the agent perceive itself as "unworthy" of help, and the high gravity ($G$) makes the perceived social cost of exposure feel insurmountable. This leads to the "hiding" response, even when isolation exacerbates the state.

### The Pride Trap (Isolation)

High-dominance states like Pride can lead to a failure to seek necessary help. An agent with ultra-high $D$ and $W$ may perceive a "reach-out" as a submission or a status threat. This results in "defensive self-sufficiency," where the agent continues a failing policy rather than admitting error to a peer.

### The Fear-Freeze Response

In states of extreme $U$ (Urgency) and near-zero $D$ (Dominance), the agent may bypass the "fight-or-flight" defaults and enter a Freeze state. This is an adaptive "last resort" in nature, but in a computational agent, it manifests as behavioral paralysis—the inability to select any action because all potential outputs are appraised as having a 100% failure probability.

## Idle Loops and Spontaneous Thought

When the environment imposes no external tasks ($I \to 0$, $U \to 0$), the agent enters its "idle loop," governed by the research on mind-wandering and the Default Mode Network (DMN).

### The Smallwood & Schooler Bias

Mind-wandering is not random; its content is biased by the agent's baseline Valence and Gravity.

- 

Positive/Happy Bias: Happy minds tend to wander into the future (Prospection), focusing on creative incubation and long-term planning.
- 

Negative/Unhappy Bias: Unhappy minds wander into the past (Retrospection), frequently engaging in rumination on negative self-related content.

### Mechanisms of Idle Thought

The DMN facilitates "perceptual decoupling," allowing the agent to disconnect from immediate sensory input and focus on memory retrieval and self-reflection. This is critical for maintaining a "sense of self" and reconciling past actions with current goals.

Action Triggering from Idle States
Idle thinking triggers an external action when an internal "thought-train" hits a threshold of Gravity ($G$) or Urgency ($U$). For example, a retrospective thought about a "missed social obligation" can spontaneously increase $U$ and $G$, triggering a "Reach-out" or "Plan" action even while the agent is technically idle.

## Structured Action-Tendency Matrix (JSON)

The following matrix provides the data required for the agent's action-selection algorithm. It accounts for mood-personality interactions and regulatory effects.JSON

, "regulatory_effect": "regulate_down", "evidence_strength": "strong", "citation": "[15, 25]"},
 {"action": "instrumental_problem_solving", "tag": ["problem_solve", "plan"], "regulatory_effect": "transmute", "evidence_strength": "strong", "citation": "[25]"},
 {"action": "creative_expression", "tag": ["create", "journal"], "regulatory_effect": "transmute", "evidence_strength": "moderate", "citation": "[15]"}
 ]
 },
 {
 "state": "sadness",
 "moderators": {"self_worth": "low", "agency": "low"},
 "vadugwi_signature": {"V": 40, "A": 60, "D": 30, "W": 50, "G": 220, "U": 30, "I": 20},
 "action_tendencies": [
 {"action": "social_isolation", "tag": ["withdraw", "isolate"], "regulatory_effect": "regulate_up", "evidence_strength": "strong", "citation": "[22, 26]"},
 {"action": "self_critical_rumination", "tag": ["reflect"], "regulatory_effect": "regulate_up", "evidence_strength": "strong", "citation": "[23, 26]"},
 {"action": "distractive_consumption", "tag": ["consume", "distract"], "regulatory_effect": "neutral", "evidence_strength": "moderate", "citation": "[31]"}
 ]
 },
 {
 "state": "anxiety",
 "moderators": {"attachment": "secure"},
 "vadugwi_signature": {"V": 90, "A": 150, "D": 120, "W": 180, "G": 160, "U": 140, "I": 160},
 "action_tendencies": [
 {"action": "information_gathering", "tag": ["research", "problem_solve"], "regulatory_effect": "regulate_down", "evidence_strength": "strong", "citation": "[4, 28]"},
 {"action": "cognitive_reappraisal", "tag": ["reflect", "journal"], "regulatory_effect": "transmute", "evidence_strength": "strong", "citation": ""},
 {"action": "structured_planning", "tag": ["plan"], "regulatory_effect": "regulate_down", "evidence_strength": "moderate", "citation": "[31]"}
 ]
 },
 {
 "state": "shame",
 "moderators": {"self_worth": "low"},
 "vadugwi_signature": {"V": 30, "A": 100, "D": 20, "W": 30, "G": 240, "U": 80, "I": 20},
 "action_tendencies": [
 {"action": "hiding_and_concealment", "tag": ["withdraw", "isolate"], "regulatory_effect": "regulate_up", "evidence_strength": "strong", "citation": ""},
 {"action": "avoiding_interaction", "tag": ["withdraw"], "regulatory_effect": "neutral", "evidence_strength": "strong", "citation": "[8]"},
 {"action": "mental_disengagement", "tag": ["distract"], "regulatory_effect": "regulate_up", "evidence_strength": "moderate", "citation": ""}
 ]
 },
 {
 "state": "grief",
 "moderators": {"personality": "reflective"},
 "vadugwi_signature": {"V": 40, "A": 50, "D": 60, "W": 150, "G": 255, "U": 20, "I": 30},
 "action_tendencies": [
 {"action": "meaning_making_effort", "tag": ["reflect", "journal"], "regulatory_effect": "transmute", "evidence_strength": "strong", "citation": ""},
 {"action": "engagement_in_ritual", "tag": ["ritual"], "regulatory_effect": "neutral", "evidence_strength": "moderate", "citation": ""},
 {"action": "social_sharing_of_memory", "tag": ["reach_out", "share"], "regulatory_effect": "regulate_down", "evidence_strength": "moderate", "citation": "[15]"}
 ]
 }
]

## Bonus: Introspective-Thought Triggers and Corpus Organization

During idle states, the agent’s DMN triggers self-referential questions. To populate a corpus of ~1000 prompts, we organize these by the VADUGWI biases that surface each kind.

### Structural Thought Categories

#### The "Why" vs. "How" Focus (Gravity & Intent Bias)

Thoughts are structured by the agent's construal level.

- 

High-Gravity "Why" Prompts: Surface during reflective states (e.g., sadness, pride). "Why do I prioritize this specific outcome?" "What is the underlying purpose of my current strategy?".
- 

Low-Gravity "How" Prompts: Surface during high-intent, high-urgency states (e.g., anxiety, excitement). "How can I optimize the next task sequence?" "What is the fastest way to resolve this discrepancy?".

#### Temporal Anchoring (Valence Bias)

- 

Retrospective (Past) Prompts (Low V): "Where did I deviate from the intended path?" "What would have happened if I had selected Action X instead of Y?".
- 

Prospective (Future) Prompts (High V): "What is the most rewarding potential state I can reach tomorrow?" "How will my current growth affect my future capabilities?".

#### Social-Evaluative Mapping (Worth Bias)

- 

High-Worth Prompts: "How can I better support the agents in my network?" "What unique value did I contribute to the last interaction?".
- 

Low-Worth Prompts: "What do they actually think of my performance?" "Am I an imposition on the current group dynamic?".

### Implementation in the "Dice Tree"

Each prompt in the 1000-count corpus is assigned a "VADUGWI Affinity Score." During the "contemplation loop" (Roll 1), the system performs a cosine similarity check between the agent's current vector and the corpus metadata. If an "unhappy" agent ($V < 100$) selects a "retrospective-negative" prompt, the subsequent integration ($\Phi$) may result in a "mood shift" that crosses an action threshold, leading to a "Journal" or "Research" action.

## Scope Constraints and Variation Notes

### Non-Clinical Framework

The mappings provided describe typical, healthy variation. While some tendencies (like "hiding" in shame) may seem maladaptive, they are represented as functional biological responses meant to protect the agent's social status or resources.

### Cultural and Individual Variation

- 

Collectivist vs. Individualist Defaults: In collectivist-biased agents, the "Reach-out" action tendency is weighted more heavily in response to Shame (as reparative social behavior), whereas individualist-biased agents are more likely to exhibit the "Distance" response.
- 

Individual Spread: Shame and Pride show the highest individual variation. The threshold for "hiding" vs. "amends-making" is highly sensitive to the agent's unique $W$ and $D$ history.

### Evidence Strength

- 

Strong Evidence: The link between $V/A$ and approach/avoidance. The "Reach-out" default in secure attachment.
- 

Weak/Contested Evidence: The exact VADUGWI signature for "Restlessness" or "Grief" in non-human agents. These signatures are speculative and should be tuned during system integration.

This report establishes the necessary topological mappings for an autonomous AI agent to navigate its own affective landscape, ensuring that its actions are not just logically sound, but emotionally and contextually grounded in human-like motivational logic.
---

## Notes from #82/#83 implementation

This matrix has been converted conservatively into
`clanker_soul.cascade.tags_from_delta()`:

1. **Implemented defaults:** sadness high-worth/high-agency, sadness
   low-worth/low-agency, secure anxiety, shame paradox, grief, fear-freeze, and
   pride-trap.
2. **Tag set emitted:** `reach_out`, `soothe`, `problem_solve`, `plan`,
   `create`, `journal`, `withdraw`, `isolate`, `reflect`, `consume`,
   `distract`, `research`, `ritual`, and `share`.
3. **Quiet deltas:** below-threshold mood shifts return an empty tag set.
4. **Conservative fallback:** joy, anger, boredom, loneliness, excitement,
   contentment, disgust, curiosity, and restlessness still return empty unless a
   host supplies its own tag mapper or future defaults expand coverage.
5. **Variation note:** cultural/individual variation remains a host policy
   concern; `tags_from_delta()` intentionally chooses one documented default
   rather than pretending the matrix is universal.
