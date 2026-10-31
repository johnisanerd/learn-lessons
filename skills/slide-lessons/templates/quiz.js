// video-lessons + slide-lessons quiz widget: instant feedback per question, running score.
// Markup: section.quiz > div.q > p.prompt + button.opt[data-why][data-correct?]...
document.querySelectorAll(".quiz").forEach((quiz) => {
  const questions = [...quiz.querySelectorAll(".q")];
  const score = quiz.querySelector(".score") || quiz.appendChild(Object.assign(document.createElement("p"), { className: "score" }));
  let answered = 0, correct = 0;

  questions.forEach((q, i) => {
    q.querySelector(".prompt")?.setAttribute("data-n", i + 1);
    const why = q.querySelector(".why") || q.appendChild(Object.assign(document.createElement("p"), { className: "why" }));
    why.hidden = true;

    q.querySelectorAll(".opt").forEach((opt) => {
      opt.type = "button";
      opt.addEventListener("click", () => {
        const isRight = opt.hasAttribute("data-correct");
        q.querySelectorAll(".opt").forEach((o) => {
          o.disabled = true;
          if (o.hasAttribute("data-correct")) o.classList.add("right");
        });
        if (!isRight) opt.classList.add("wrong");
        why.textContent = opt.dataset.why || "";
        why.className = "why " + (isRight ? "right" : "wrong");
        why.hidden = false;

        answered += 1;
        if (isRight) correct += 1;
        score.textContent = answered === questions.length
          ? `${correct} / ${questions.length}. Say "next" in Claude Code when you're ready; I'll check what stuck first.`
          : `${correct} / ${answered} so far`;
      });
    });
  });
});
