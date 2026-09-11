# Coach and Scout Question Test Set
## Film Clip Retrieval App (SkillCorner Tracking and Event Data)

Purpose: This is a seed test set of realistic coach and scout questions the app should
be able to answer by retrieving tailored sequences from SkillCorner tracking and event
data. Categories are designed to stress-test every capability the retrieval architecture
will need: player identification, spatial reasoning, event-type filtering, event
sequencing, game-state awareness, comparative or relational reasoning, and absence
(negative) detection.

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

## 2. Spatial / Positional Questions
Tests geometric reasoning over tracking data: zones, distances, angles, and shape.

7. Show me all plays where their left winger cut inside at the top of the box.
8. Show me every time a player received the ball in the half-space between the lines.
9. Show me all moments their back four dropped deeper than the halfway line while defending a lead.
10. Show me every cross delivered from the byline versus deeper wide areas.
11. Show me all moments where their fullback pushed high and left space in behind.
12. Show me every sequence where their midfield block compressed into a narrow shape.

## 3. Event-Type Questions
Tests filtering on tagged discrete events, largely answerable from event data alone.

13. Show me all shots taken from outside the box.
14. Show me every corner kick they conceded.
15. Show me all through balls attempted in the final third.
16. Show me every interception in their own defensive third.
17. Show me all offside calls against their front line.
18. Show me every long ball attempted from their goalkeeper.

## 4. Sequence / Chain-of-Events Questions
Tests linking multiple events across time into one coherent passage of play.

19. Show me every sequence that started with a regain in midfield and ended in a shot within 10 seconds.
20. Show me all build-up sequences that began with a goal kick and reached the final third.
21. Show me every counter-attack that started from a defensive header clearance.
22. Show me all possessions where they strung together five or more passes before losing the ball.
23. Show me every sequence where a give-and-go combination led to a shot.
24. Show me all corner kick routines that resulted in a shot within 6 seconds of delivery.

## 5. Game-State / Temporal Questions
Tests awareness of score, match clock, and situational context, not just raw events.

25. Show me their defensive shape in the last 15 minutes while protecting a one-goal lead.
26. Show me every transition moment in the first 10 minutes of the match.
27. Show me how they defended set pieces after going down a goal.
28. Show me all moments of game management, like slow throw-ins or keep-ball, after the 80th minute.
29. Show me their pressing intensity in the 5 minutes right after they conceded.

## 6. Comparative / Relational Questions
Tests reasoning about relative positioning and matchups between two or more players.

30. Show me every moment their fullback was isolated one-on-one against a winger.
31. Show me all situations where their center back was dragged out of position by a striker's movement.
32. Show me every time their double pivot got split by a vertical pass.
33. Show me moments where an attacker had a numerical advantage against the last defender.
34. Show me all foot races between their center back and an opposing striker in behind.

## 7. Negative / Absence Questions
Tests detecting the lack of an event or action, generally the hardest category since it
requires reasoning over what did not happen rather than pattern-matching a tag.

35. Show me every time their striker did not press the center back after a goal kick.
36. Show me all moments their winger tracked back but failed to make a recovery run.
37. Show me sequences where they had a numerical advantage on the counter but did not take a shot.
38. Show me all corners where no player attacked the near post.
39. Show me moments their fullback was beaten and no covering defender rotated across.

---

## Notes for Architecture Design
- Categories 1 through 3 are largely answerable from event data plus player IDs alone.
- Categories 2, 4, 5, and 6 require tracking data and spatial or temporal reasoning layered on top of events.
- Category 7 will likely need the most novel architecture work, since it requires querying
  for the absence of a pattern within a defined window, rather than a positive match.
- Many real coach questions will blend categories, for example a spatial trigger tied to a
  specific player during a specific game state, so the retrieval system should treat these
  as composable filters rather than mutually exclusive buckets.
