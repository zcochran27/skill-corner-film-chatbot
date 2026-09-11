# Coach and Scout Question Test Set
## Film Clip Retrieval App (SkillCorner Tracking and Event Data)

Purpose: This is a seed test set of realistic coach and scout questions the app should
be able to answer by retrieving tailored sequences from SkillCorner tracking and event
data. Categories are designed to stress-test every capability the retrieval architecture
will need: player identification, spatial reasoning, event-type filtering, event
sequencing, game-state awareness, comparative or relational reasoning, absence
(negative) detection, and composite questions that blend several of the above at once.

---

## 1. Player-Specific Questions
Tests the system's ability to filter and retrieve moments tied to a named or
role-identified individual.

1. Show me every time their left winger received the ball in the final third.
2. Show me all of number 9's touches inside the penalty box.
3. Show me every carry their right back made past the halfway line.
4. Show me all defensive actions by their center back pairing in the second half.
5. Show me every shot taken by their captain this match.
6. Show me all moments the goalkeeper played a pass into midfield under pressure.
7. Show me every sprint their right winger made without the ball.
8. Show me all one-touch passes played by their number 8 in the final third.
9. Show me every off-ball run by their left back that went in behind the last line.
10. Show me all long balls their goalkeeper played that were classified as high passes.
11. Show me every time their center forward dropped off into midfield to link play.

## 2. Spatial / Positional Questions
Tests geometric reasoning over tracking data: zones, distances, angles, and shape.

12. Show me all plays where their left winger cut inside at the top of the box.
13. Show me every time a player received the ball in the half-space between the lines.
14. Show me all moments their back four dropped deeper than the halfway line while defending a lead.
15. Show me every cross delivered from the byline versus deeper wide areas.
16. Show me all moments where their fullback pushed high and left space in behind.
17. Show me every sequence where their midfield block compressed into a narrow shape.
18. Show me every reception inside the penalty area that came from a cutback.
19. Show me all moments a player was inside the opponent's defensive shape when they received the ball.
20. Show me every pass that broke the opponent's last defensive line.
21. Show me all crosses delivered into the six-yard box versus the penalty spot area.
22. Show me every time their winger received the ball wide with a fullback still goal-side of them.

## 3. Event-Type Questions
Tests filtering on tagged discrete events, largely answerable from event data alone.

23. Show me all shots taken from outside the box.
24. Show me every corner kick they conceded.
25. Show me all through balls attempted in the final third.
26. Show me every interception in their own defensive third.
27. Show me all offside calls against their front line.
28. Show me every long ball attempted from their goalkeeper.
29. Show me every clearance they made under pressure inside their own box.
30. Show me all quick passes they played out from the back.
31. Show me every give-and-go combination they attempted.
32. Show me all headers won in their own penalty area from set pieces.
33. Show me every pass targeted at a teammate rated as a dangerous, difficult target.

## 4. Sequence / Chain-of-Events Questions
Tests linking multiple events across time into one coherent passage of play.

34. Show me every sequence that started with a regain in midfield and ended in a shot within 10 seconds.
35. Show me all build-up sequences that began with a goal kick and reached the final third.
36. Show me every counter-attack that started from a defensive header clearance.
37. Show me all possessions where they strung together five or more passes before losing the ball.
38. Show me every sequence where a give-and-go combination led to a shot.
39. Show me all corner kick routines that resulted in a shot within 6 seconds of delivery.
40. Show me every possession that included a give-and-go and ended in a shot.
41. Show me all sequences where a line-breaking pass was followed by a shot within 8 seconds.
42. Show me every pressing chain of at least three consecutive engagements that won the ball back.
43. Show me all sequences starting from a throw-in that reached the penalty box.
44. Show me every build-up sequence broken up by an opponent's counter-press within 4 seconds.

## 5. Game-State / Temporal Questions
Tests awareness of score, match clock, and situational context, not just raw events.

45. Show me their defensive shape in the last 15 minutes while protecting a one-goal lead.
46. Show me every transition moment in the first 10 minutes of the match.
47. Show me how they defended set pieces after going down a goal.
48. Show me all moments of game management, like slow throw-ins or keep-ball, after the 80th minute.
49. Show me their pressing intensity in the 5 minutes right after they conceded.
50. Show me their build-up pattern in the first 5 minutes after kickoff.
51. Show me how they responded in the 2 minutes immediately after scoring.
52. Show me all direct-play sequences they used while chasing the game in the last 10 minutes.
53. Show me every set piece they defended in stoppage time.
54. Show me whether their passing got noticeably more direct once they were leading by two or more goals.

## 6. Comparative / Relational Questions
Tests reasoning about relative positioning and matchups between two or more players.

55. Show me every moment their fullback was isolated one-on-one against a winger.
56. Show me all situations where their center back was dragged out of position by a striker's movement.
57. Show me every time their double pivot got split by a vertical pass.
58. Show me moments where an attacker had a numerical advantage against the last defender.
59. Show me all foot races between their center back and an opposing striker in behind.
60. Show me every moment a fullback had less than two meters of separation from an opposing winger.
61. Show me all moments their double pivot was outnumbered, with more opponents than teammates ahead of the ball.
62. Show me every defensive engagement their center back won one-on-one against an isolated striker.
63. Show me all moments an attacker overtook two or more defenders on a single run.
64. Show me every passing option that was rated both dangerous and difficult to defend at the same time.

## 7. Negative / Absence Questions
Tests detecting the lack of an event or action, generally the hardest category since it
requires reasoning over what did not happen rather than pattern-matching a tag.

65. Show me every time their striker did not press the center back after a goal kick.
66. Show me all moments their winger tracked back but failed to make a recovery run.
67. Show me sequences where they had a numerical advantage on the counter but did not take a shot.
68. Show me all corners where no player attacked the near post.
69. Show me moments their fullback was beaten and no covering defender rotated across.
70. Show me every corner they conceded where no defender contested the first ball.
71. Show me all moments a passing option was open inside the box but was not targeted.
72. Show me every sequence where they broke the opponent's first defensive line but did not create a shot.
73. Show me all moments they had a pressing chain going but it did not end in winning the ball back.
74. Show me every transition moment where they had numbers back but chose not to counter-press.

## 8. Composite / Blended Questions
Tests whether the retrieval architecture can compose filters across multiple gates at
once, since most real coach questions don't stay inside a single category.

75. Show me every time their left winger cut inside at the top of the box while protecting a one-goal lead in the last 15 minutes.
76. Show me all high-speed recovery runs by their right back in the 10 minutes right after they conceded.
77. Show me every give-and-go in the attacking third that led to a shot within 6 seconds.
78. Show me all moments their captain was isolated one-on-one in the box during a corner they conceded.
79. Show me every pressing chain by their front two that started within 5 seconds of a goal kick and ended in a regain.
80. Show me every sequence where a substitute broke the opponent's last defensive line and the possession ended in a shot within 10 seconds.

---

## Notes for Architecture Design
- Categories 1 through 3 are largely answerable from event data plus player IDs alone.
- Categories 2, 4, 5, and 6 require tracking data and spatial or temporal reasoning layered on top of events.
- Category 7 will likely need the most novel architecture work, since it requires querying
  for the absence of a pattern within a defined window, rather than a positive match.
- Category 8 is a deliberate stress test for composability: every question in it reuses
  gates already covered by categories 1-7 (event-type, spatial, sequence, game-state,
  comparative, negative/absence, player-specific), just combined 2-3 at a time. If the
  8-gate parsing chain is implemented as independent, composable filters rather than one
  query type per question, category 8 should require no new gate logic at all — it's a
  check on the architecture, not a new capability.
- Question 80 (and any future "substitute" or lineup-status question) needs player
  start/substitution data joined in from `{id}_match.json`, not just the dynamic events
  table — worth flagging as its own enrichment step if more questions like it get added.
- Many real coach questions will blend categories, for example a spatial trigger tied to a
  specific player during a specific game state, so the retrieval system should treat these
  as composable filters rather than mutually exclusive buckets.
