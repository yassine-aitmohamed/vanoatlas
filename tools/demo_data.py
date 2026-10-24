import json
import sys
import urllib.request
from datetime import date, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8770"
T = date.today()


def d(n):
    return (T + timedelta(days=n)).isoformat()


def post(table, data):
    req = urllib.request.Request(f"{BASE}/api/{table}", data=json.dumps(data).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode()).get("id")


p1 = post("projects", {"title": "Thesis — Chapter 2", "description": "Deformations of Lie algebroids and their cohomology.", "status": "Active", "domain": "Differential geometry", "deadline": d(75)})
p2 = post("projects", {"title": "Paper with my advisor", "description": "Explicit examples and a classification result.", "status": "Active", "domain": "Algebra", "deadline": d(40)})
p3 = post("projects", {"title": "Teaching — Linear algebra", "description": "Exercise sessions and grading.", "status": "Active", "domain": "Teaching", "deadline": d(50)})
p4 = post("projects", {"title": "Conference talk", "description": "20-minute talk and slides.", "status": "Planning", "domain": "Communication", "deadline": d(28)})

tasks = [
    (p1, "Write Section 2.3: the deformation complex", "In Progress", "High", d(5), 120),
    (p1, "Check the spectral sequence argument", "To Do", "High", d(9), 90),
    (p1, "Compute the low-dimensional examples", "To Do", "Normal", d(14), 60),
    (p2, "Draft the introduction", "In Review", "High", d(3), 90),
    (p2, "Read the two references the referee suggested", "To Do", "Normal", d(7), 60),
    (p2, "Fix the notation in Section 4", "Done", "Normal", d(-2), 45),
    (p3, "Prepare exercise sheet 6", "To Do", "Normal", d(2), 60),
    (p3, "Grade quizzes", "In Progress", "Low", d(4), 90),
    (p4, "Outline of the talk", "To Do", "Normal", d(10), 45),
    (p4, "Make the slides", "To Do", "High", d(20), 180),
    (None, "Renew library books", "To Do", "Low", d(6), 15),
]
for pid, title, status, prio, dl, est in tasks:
    post("tasks", {"project_id": pid, "title": title, "status": status, "priority": prio, "deadline": dl,
                   "estimate_min": est, "task_type": "Research", "planned_date": T.isoformat() if status == "In Progress" else ""})

post("meetings", {"title": "Weekly meeting with my advisor", "meeting_date": d(2), "meeting_time": "15:30", "start_time": "15:30", "end_time": "16:30", "meeting_type": "Advisory Meeting", "location": "Office 3.12", "project_id": p2, "recur_freq": "weekly", "recur_until": d(120)})
post("meetings", {"title": "Geometry seminar", "meeting_date": d(4), "meeting_time": "14:00", "start_time": "14:00", "end_time": "15:00", "meeting_type": "Seminar", "location": "Room B-201"})
post("meetings", {"title": "Reading group", "meeting_date": d(1), "meeting_time": "10:00", "start_time": "10:00", "end_time": "11:30", "meeting_type": "Group", "location": "Library"})

for t, c, h1, h2, done in [("Morning reading: one paper", "Research", "08:00", "09:00", 1), ("Deep work: Section 2.3", "Research", "09:00", "11:00", 0),
                           ("Lunch break", "Personal", "12:00", "13:00", 0), ("Exercise sheet 6", "Teaching", "13:30", "14:30", 0),
                           ("Walk", "Health", "17:30", "18:15", 0)]:
    post("daily_tasks", {"title": t, "category": c, "done": done, "task_date": T.isoformat(), "task_time": h1, "task_time_end": h2})

for t, md, pid in [("Chapter 2 draft to advisor", d(30), p1), ("Submit the paper", d(40), p2), ("Talk", d(28), p4)]:
    post("milestones", {"title": t, "milestone_date": md, "project_id": pid, "done": 0})

post("notes", {"title": "Deformation complex", "content": "The deformation complex of a Lie algebroid $A$ controls its deformations. Key fact: $H^2$ contains the obstructions. See [[Spectral sequence]].", "tags": "algebroids,cohomology", "project_id": p1, "category": "Research"})
post("notes", {"title": "Spectral sequence", "content": "Filtration by form degree. The $E_1$ page is computed from the isotropy. Linked to [[Deformation complex]].", "tags": "cohomology", "project_id": p1, "category": "Research"})
post("notes", {"title": "Ideas for the talk", "content": "Start with one picture. One theorem, one example, one open question.", "tags": "talk", "project_id": p4, "category": "New ideas"})
post("results", {"label": "Lemma 2.4", "title": "Vanishing in degree one", "kind": "Lemma", "status": "Proved", "statement": "If $A$ is transitive then $H^1_{def}(A)=0$.", "project_id": p1})
post("results", {"label": "Theorem 2.7", "title": "Rigidity", "kind": "Theorem", "status": "In progress", "statement": "Compact transitive Lie algebroids are rigid.", "deps": "Lemma 2.4", "project_id": p1})
post("refs", {"bibkey": "Crainic2008", "title": "Deformations of Lie brackets: cohomological aspects", "authors": "M. Crainic, I. Moerdijk", "year": "2008", "venue": "J. Eur. Math. Soc.", "status": "Read", "project_id": p1})
post("inbox", {"title": "Ask about the travel grant", "source": "Capture"})
post("achievements", {"title": "Paper accepted", "win_type": "Publication", "win_date": d(-12)})
print("demo data added to", BASE)
