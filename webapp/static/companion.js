"use strict";

// Small, preset companion interactions. No model calls or project mutations.
(() => {
  const greeting = document.querySelector("#dragon-greeting");
  const tip = document.querySelector("#companion-tip");
  const next = document.querySelector("#companion-next");
  const cheer = document.querySelector("#companion-cheer");
  if (!greeting || !tip || !next || !cheer) return;

  const messages = {
    empty: {
      default: "“我还没想清楚”也没关系。把你最在意的那个问题告诉我。",
      hello: ["你好呀，我是小研！先写下一个好奇的问题，我们从这里开始。", "收到你的招呼啦！不用一开始就有完整课题，一句话也能成为起点。"],
      next: ["先写下研究想法，选好偏好、资源和时间，再点“开始探索”。", "不知从哪里写？试试一句：“我想弄清……，因为……”。也可以点一个样例看看。"],
      cheer: ["模糊的兴趣也值得认真对待。今天把问题写下来，就已经向前一步了。", "先允许自己不知道答案。一个具体的问题，比一开始追求完美更有帮助。"]
    },
    clarifying: {
      default: "先补充一点背景，方向才会更适合你。暂时不知道的地方也可以说出来。",
      hello: ["我在这里！先把你的条件说清楚，研究方向才有落脚的地方。", "你好呀！不知道的地方也可以写“暂不确定”，不必勉强给出答案。"],
      next: ["回答当前的澄清问题，再提交回答继续。暂不确定时，可以直接说明。", "想一想已有基础、可用资源和时间。把这些真实条件写下来，方向会更容易比较。"],
      cheer: ["把“我能做什么”说清楚，就是一个很好的开始。我们一步一步来。", "发现自己的约束，和发现一个好想法一样有价值。先把这几个问题补充完整。"]
    },
    directions_ready: {
      default: "推荐是一个参考。看看依据和约束，再选你最想走的方向。",
      hello: ["方向卡已经准备好了！我会陪你比较，最终从哪一条开始由你决定。", "招呼收到！先看看每个方向的首轮验证和停止条件，再做自己的选择。"],
      next: ["展开方向卡的依据、验证与风险，比较资源和停止条件；想清楚后再点“选择这个方向”。", "推荐不等于你的选择。核对来源与阅读范围，再决定哪个方向最适合现在的你。"],
      cheer: ["不必挑“看起来最大”的课题。能认真验证一个小问题，也是一条好的研究路。", "你可以比较，也可以重新考虑。值得做的选择，要同时照顾好奇心和实际条件。"]
    },
    topic_selected: {
      default: "从最小的验证开始。记录结果，也记录让你改变主意的证据。",
      hello: ["选好方向了！先把首轮计划留下来，再准备第一个小验证。", "你好呀！计划是起点，真正的结果还要靠你去核查和验证。"],
      next: ["检查首轮步骤、对照基线和停止条件，可导出计划，或到“实验工作台”核对可运行范围。", "看看第一步需要的数据和资源；若不适合，可以“重新比较方向”。你的选择会留在历史里。"],
      cheer: ["方向已经清楚了。接下来认真做好一个小验证，比一次走很远更重要。", "结果和预期不一样也有价值。保留证据，它可能帮你找到更好的下一步。"]
    }
  };
  let stage = messages[document.body.dataset.researchStage] ? document.body.dataset.researchStage : "empty";
  let projectId = null;
  let indexes = { hello: 0, next: 0, cheer: 0 };
  let effectTimer = null;
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function resetEffect() {
    clearTimeout(effectTimer);
    effectTimer = null;
    greeting.classList.remove("is-greeting", "is-cheering");
  }
  function animate(kind) {
    resetEffect();
    // One CSS effect at a time, including rapid repeated taps.
    if (!motion.matches) void greeting.offsetWidth;
    greeting.classList.add(kind === "cheer" ? "is-cheering" : "is-greeting");
    effectTimer = setTimeout(resetEffect, motion.matches ? 450 : 850);
  }
  function respond(kind) {
    const options = messages[stage][kind];
    tip.textContent = options[indexes[kind] % options.length];
    indexes[kind] += 1;
    animate(kind);
  }
  greeting.addEventListener("click", () => respond("hello"));
  next.addEventListener("click", () => respond("next"));
  cheer.addEventListener("click", () => respond("cheer"));
  document.addEventListener("yanzheng:stage", event => {
    const detail = event.detail || {};
    const newStage = messages[detail.stage] ? detail.stage : "empty";
    const newProjectId = typeof detail.projectId === "string" ? detail.projectId : null;
    if (stage === newStage && projectId === newProjectId) return;
    stage = newStage;
    projectId = newProjectId;
    indexes = { hello: 0, next: 0, cheer: 0 };
    resetEffect();
    tip.textContent = messages[stage].default;
  });
  if (typeof motion.addEventListener === "function") motion.addEventListener("change", resetEffect);
  tip.textContent = messages[stage].default;
})();
