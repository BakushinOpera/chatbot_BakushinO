/* 房间灰模：一份场景，两种看法——书桌视角（透视，对着参考图）和房间视角（等距）。
 *
 * 【房间页面用的副本】从灰模原型 greyBoxTest/scene.js（不在仓库里）复制（2026-10-24）。改动只有三处：
 *   · 引用的是 room3d-persp.js、全局名叫 RoomScene3D；
 *   · DESK / CHAIR / CHAIR_F / MON / PILE / NOTEBOOK / NB_F 这些和状态无关的常量从 buildScene 里提到了模块级，并导出 LAYOUT（页面按它摆书、摆小物件、摆角色）；
 *   · 场景本身（形体、尺寸、位置）一个字没动。灰模改了以后，把新版和这份对一下再同步，tests/room3d_test.js 会检查家具位置有没有对上。
 *
 * 坐标（cm）就是书桌相机的坐标：x 向右、y 向前（离相机越来越远）、z 向上；原点 = 书桌相机所在的地面点，相机在 (0,0,100) 朝南看。
 * 房间方位：窗户在南墙 → 窗墙 = 南墙；+y = 南、-y = 北；相机朝南时右手边是西 → +x = 西、-x = 东。
 *
 * 房间 x -225（东墙）~365（西墙）、y 30（北墙）~360（南墙，窗墙）= 590×330cm（比例 1.8），高 270。
 *   - 书桌相机在北墙外面（北墙就在它前方 30cm），像游戏里一样看不到这面墙；书桌视角里隐藏北墙。
 *   - 窗墙从参考图推算的 262cm 推到了 360cm（要让房间进深正常）：窗、沙发、边柜按真实大小重摆，所以书桌视角里它们比参考图小一圈。
 *     桌子、桌上的东西的相对位置沿用参考图，但为了放得进「现实比例的 L 桌」，都往桌沿挪近了（见下）。
 *   - 书桌相机在原地逆时针转了 DESK_YAW°（朝南偏东）：床、床头柜、衣柜、西墙都出了画面（视野右缘偏离正南 45° − yaw），椅子靠背落在画面中心略偏西（默认镜头还往东横移了 15cm）；
 *     东墙在左边露出来，书桌最东头和书架都靠着它。相机到椅子靠背的视线上不放高的东西（见桌上的东西）。
 * L 形书桌顶在东北角：下边（240×65）贴北墙，竖边（150×65）贴东墙。
 * 房间视角是真等距，从东南上方看向西北：北墙、西墙在后面；前面的东墙、南墙（窗墙）和天花板拆掉。
 */
(function (root) {
  "use strict";
  const P = root.RoomPersp || require("./room3d-persp.js");
  const { frustum, cyl, X, Z, D } = P;

  // 房间边界（墙的内侧面）、墙厚、墙高
  const ROOM = { xe: -225, xw: 365, yn: 30, ys: 360, H: 270, T: 8 };      // xe 东墙、xw 西墙、yn 北墙、ys 南墙（窗墙）
  const DOOR = { y0: 92, y1: 182, h: 210 };                             // 西墙上的门洞：90cm 宽、210 高（只留位置，不画门）
  const WIN = { x0: -190, x1: 160, sill: 90, top: 250 };                // 窗洞（真实大小 350 宽）
  // 书桌相机的转角：绕相机自己所在的竖直轴向东转（逆时针）这么多度。0 = 正对南墙（对着参考图）。
  // 转到 23° 是为了让床完全出画（床的西南角 (154,354) 在偏离正南 23.5° 的方向，视野右缘 = 45° − yaw），同时椅子靠背落在画面中心略偏西。
  const DESK_YAW = 23;
  // 书桌相机的横向位置：沿北墙外面平移（+x = 往西、-x = 往东）；默认 -15（往东 15cm），查看页上有滑块可以拖。
  const DESK_X = -15;

  // 桌上的两个状态（状态接口）：buildScene({ state })、查看页的 A/B 开关、build.js 的输出和检查都用它。
  const STATES = {
    A: { label: "A 笔记本叠放", src: "所有书本叠成一摞（最西边那本当底，上面是 5 本书、她写字的本子，最上面是合上的笔记本），桌上没有摊开的东西" },
    B: { label: "B 摊开的笔记本", src: "笔记本从书摞上拿下来、摊开（封面 + 左右两页 + 书脊缝，每页 16.2×23.2），摆在显示器和椅子之间的连线上；右手写字的角度：页面朝上的方向比椅子朝向逆时针转 25°，右页在人正前方偏右；其余书本还叠在最西头" },
  };
  const DEFAULT_STATE = "A";

  // 每组的名字和说明（预览页的开关、尺寸表都用它）。hide：这一组默认在哪个视角里隐藏（desk 书桌视角 / room 房间视角）
  const GROUPS = [
    { id: "floor", label: "地板", src: "整个房间的地面，带一圈厚度", hide: [] },
    { id: "ceiling", label: "天花板", src: "z=270；书桌视角里能看到它的底面，房间视角里拆掉", hide: ["room"] },
    { id: "wallnorth", label: "北墙", src: "y=30，书桌贴着它；在书桌相机前方 30cm，书桌视角里看不到（相机在墙外）", hide: ["desk"] },
    { id: "walleast", label: "东墙", src: "x=-225，书桌、书架、沙发的东端都贴着它；书桌视角左缘能看到；房间视角里拆掉", hide: ["room"] },
    { id: "wall", label: "南墙（窗墙）+ 窗框 + 墙上的海报", src: "y=360：窗洞 350 宽（x -190~160）、窗台 90、4 根窗框竖挺、窗洞上沿的横梁、墙上挂的深蓝色海报和窄板；房间视角里拆掉", hide: ["room"] },
    { id: "wallwest", label: "西墙（带门洞）", src: "x=365；y 92~182 留 90cm 宽的门洞，不画门", hide: [] },
    { id: "outdoor", label: "窗外（天空）", src: "远处的一块天空（窗外的树和坡已经删掉）", hide: ["room"] },
    { id: "sofa", label: "沙发", src: "背贴南墙、东端贴东墙，长 260（x -225~35）、深 66、座面 45、靠背 92；西端离床约 1.2m" },
    { id: "corner", label: "窗下矮柜 + 落地灯", src: "沙发和床之间的墙边：矮柜 100×40×85（在窗台以下），橙色灯罩的落地灯" },
    { id: "desk", label: "书桌（标准 L 形，东北角）", src: "桌面高 75：下边 = 贴北墙的 240×65（x -225~15，y 30~95）；竖边 = 贴东墙的 65 宽、从北墙起 150 长（x -225~-160，到 y=180）；内角在 (-160,95)" },
    { id: "props", label: "桌上的小东西", src: "显示器（靠墙、屏幕正对椅子）、杯子（写字区东边）；书摞（最西边那本当底 + 5 本书 + 她写字的本子，状态 A 里最上面还有合上的笔记本）；竖边：红色机器 + 香薰瓶（靠内角）、玩偶（南端）；状态 B：笔记本摊开（左右两页）在显示器和椅子之间；视线那一条（x -30~-5）上只放平的东西" },
    { id: "hutch", label: "小书架", src: "竖边上靠东墙的小书架（带书、花盆，开口朝西）" },
    { id: "girlchair", label: "桌前的椅子（朝向显示器）", src: "没有扶手；在书桌后面、坐在 L 的凹角里，座面朝显示器（朝北偏东约 26°，画面里朝左前方），不压在桌下" },
    { id: "bookcase", label: "书架", src: "原来的书架改成单独的落地书架：靠东墙、正面朝西，放在竖边南端（y=180）和沙发（y=294）之间的墙边；宽改成 104（原 120）才塞得进去，进深 40、高 225，4 层均分" },
    { id: "foodtable", label: "食物桌", src: "贴北墙，在书桌西端和衣柜之间：140×50（x 60~200），高 75，下面一层隔板；放吃的（v10）" },
    { id: "wardrobe", label: "衣柜", src: "贴北墙、西侧贴西墙，门朝南；130×56×210" },
    { id: "bed", label: "床", src: "在西南角：床头靠西墙、长边靠南墙，205×120；离沙发约 1.2m" },
    { id: "nightstand", label: "床头柜", src: "在床头的北侧、靠西墙，抽屉朝东，约 42×45×50" },
  ];

  const DESK = 75;                                     // 桌面高度
  const CHAIR = [-60, 142];                            // 椅子（座面）中心：桌上的东西按它摆
  const MON = [-108, 42];                              // 显示器中心：靠北墙、屏幕正对椅子
  const CHAIR_F = (() => { const dx = MON[0] - CHAIR[0], dy = MON[1] - CHAIR[1], l = Math.hypot(dx, dy); return [dx / l, dy / l]; })();   // 椅子朝向：对准显示器
  const PILE = [0, 74];                                // 书摞的中心：下边最西头，在视线那一条的西边
  // 状态 B 的摊开的笔记本：放在椅子→显示器的连线上、离椅子座面中心 68cm（人伸手写字够得着、不越过桌沿 y=95）；
  // 页面朝上的方向比椅子朝向逆时针转 NB_TILT°——右手写字时纸要往左歪 20~30°，前臂才和字行垂直；转完以后它几乎和桌沿平行。
  const NOTEBOOK = [CHAIR[0] + 68 * CHAIR_F[0], CHAIR[1] + 68 * CHAIR_F[1]];
  const NB_TILT = 25;
  const NB_F = (() => { const b = Math.atan2(-CHAIR_F[0], -CHAIR_F[1]) - (NB_TILT * Math.PI) / 180; return [-Math.sin(b), -Math.cos(b)]; })();   // 朝向角（从北往东量）= 椅子的 − NB_TILT

  function buildScene({ state = DEFAULT_STATE } = {}) {
    if (!STATES[state]) throw new Error("没有这个状态：" + state + "（可选 " + Object.keys(STATES).join("、") + "）");
    const O = [];
    // 只在某个状态里出现的东西：tag("A", () => …) 里新建的形体都带上 st = "A"，最后按 state 过滤
    const tag = (st, fn) => { const n = O.length; fn(); for (let i = n; i < O.length; i++) O[i].st = st; };
    // 方块 / 台体 / 圆柱的简写
    const box = (g, name, tone, x0, x1, y0, y1, z0, z1) => O.push(frustum(g, name, tone, x0, x1, y0, y1, z0, z1));
    const fru = (g, name, tone, x0, x1, y0, y1, z0, z1, k) => O.push(frustum(g, name, tone, x0, x1, y0, y1, z0, z1, k));
    const prism = (g, name, tone, cx, cyy, rx, ry, z0, z1, n, k) => O.push(cyl(g, name, tone, cx, cyy, rx, ry, z0, z1, n, k));
    // 按参考图上的正面矩形摆（只给窗外的树、坡用）：u 横向像素范围，v 纵向像素范围，d 正面离相机多远，t 往后的厚度
    const trace = (g, name, tone, [u0, u1], [v0, v1], d, t, k) => O.push(frustum(g, name, tone, X(u0, d), X(u1, d), d, d + t, Z(v1, d), Z(v0, d), k));
    const { xe, xw, yn, ys, H, T } = ROOM;

    /* ───────── 地板、天花板、北墙（贴书桌）、东墙、西墙（带门洞） ───────── */
    box("floor", "floor", 0.93, xe - T, xw + T, yn - T, ys + 10, -8, 0);
    box("ceiling", "ceiling", 0.9, xe - T, xw + T, yn - T, ys + 10, H, H + 8);
    box("wallnorth", "wall-north", 0.7, xe, xw, yn - T, yn, 0, H);
    box("walleast", "wall-east", 0.7, xe - T, xe, yn - T, ys, 0, H);
    box("wallwest", "wall-west-n", 0.7, xw, xw + T, yn - T, DOOR.y0, 0, H);
    box("wallwest", "wall-west-s", 0.7, xw, xw + T, DOOR.y1, ys, 0, H);
    box("wallwest", "wall-west-header", 0.7, xw, xw + T, DOOR.y0, DOOR.y1, DOOR.h, H);

    /* ───────── 南墙（窗墙）：窗洞 350 宽、窗台 90、窗洞顶 250；窗框竖挺的位置沿用参考图（在窗洞里的相对位置） ───────── */
    {
      const dw = ys, t = 10;
      box("wall", "wall-left", 0.72, xe - T, WIN.x0, dw, dw + t, 0, H);          // 东段（画面左边）
      box("wall", "wall-right", 0.72, WIN.x1, xw + T, dw, dw + t, 0, H);         // 西段（画面右边），把西南角的墙角一起盖住
      box("wall", "wall-under", 0.66, WIN.x0, WIN.x1, dw, dw + t, 0, WIN.sill);
      box("wall", "wall-header", 0.72, WIN.x0, WIN.x1, dw, dw + t, WIN.top, H);  // 窗洞上沿的横梁
      box("wall", "sill", 0.82, WIN.x0, WIN.x1, dw - 12, dw, WIN.sill - 4, WIN.sill);
      [[505, 562], [948, 992], [1095, 1130], [1377, 1418]].forEach(([a, b], i) => box("wall", `mullion-${i + 1}`, 0.25, X(a, 262), X(b, 262), dw - 8, dw, WIN.sill, WIN.top));
      box("wall", "poster", 0.2, 200, 300, dw - 3, dw, 150, 235);                // 挂在窗洞西侧的墙上（床的上方）
      box("wall", "wall-panel", 0.24, 172, 194, dw - 3, dw, 170, 240);
    }

    /* ───────── 窗外：几个大块体，只表示树冠和坡的位置（比室内亮，才看得出是窗外） ───────── */
    box("outdoor", "sky", 1, -1200, 1200, 900, 920, 0, 800);
    // 窗外的树和坡删掉了（只留天空，天气动画画在天空上）

    /* ───────── 沙发：背贴南墙、东端贴东墙，260 长（现实长度）；西端 x=35 离床（x≥154）约 1.2m ───────── */
    box("sofa", "seat", 0.78, xe, 35, ys - 66, ys, 0, 45);
    box("sofa", "back", 0.8, xe, 35, ys - 12, ys, 45, 84);                      // 靠背在窗台（86）以下

    /* ───────── 沙发和床之间的墙边：窗下矮柜（在窗台 90 以下）+ 落地灯 ───────── */
    box("corner", "sideboard", 0.32, 45, 145, ys - 40, ys, 0, 85);
    prism("corner", "lamp-stem", 0.85, 60, 305, 3.5, 3.5, 0, 128, 8);
    fru("corner", "lamp-shade", 0.85, 42, 78, 287, 323, 128, 154, 0.8);

    /* ───────── 书桌：标准 L 形，顶在东北角 ─────────
     * 下边 = 贴北墙的 240×65：x -225~15，y 30~95；竖边 = 贴东墙的 65 宽、从北墙起 150 长：x -225~-160，y 95~180（和下边接在一起）。
     * 内角在 (-160, 95)，椅子坐在内角外面的凹角里。 */
    box("desk", "top-lower", 0.42, xe, 15, yn, 95, 70, 75);
    box("desk", "top-upper", 0.42, xe, -160, 95, 180, 70, 75);
    [[-221, 34], [-221, 175], [-165, 175], [10, 90], [10, 34], [-100, 90]].forEach(([x, y], i) => box("desk", `leg-${i}`, 0.36, x, x + 5, y, y + 5, 0, 70));
    // 说明：腿 [-221,34]（东北角）、[-221,175]（东南角）、[-165,175]（竖边南端西侧）、[10,34]/[10,90]（下边西端）、[-100,90]（下边中间）

    /* ───────── 桌上的东西：按「人坐在椅子上怎么用」摆；书本都叠成一摞，有两个状态（STATES）─────────
     * 椅子朝显示器（CHAIR_F），椅子中心 CHAIR，显示器中心 MON。台灯、笔筒都删掉了。
     *  - 下边（贴北墙那条）是工作区：显示器靠墙、屏幕正对椅子（所以斜着摆）；杯子在写字区的东边（人的右手边）；
     *  - 所有书本叠成一摞，立在下边最西头（最西边那本当底），在视线那一条（x -30~-5）的西边，不挡相机到椅子的视线；
     *      状态 A：笔记本合上，叠在摞的最上面；
     *      状态 B：笔记本从摞上拿下来、摊开（左右两页），摆在显示器和椅子之间、按右手写字的角度斜放；
     *  - 竖边（东边那条桌面，人的右前方）放不常动的：红色机器 + 香薰瓶靠近内角，小书架靠东墙（开口朝西、朝人），玩偶在南端。 */
    {
      const rb = (g, name, tone, cx, cy, hx, hy, f, z0, z1, k) => O.push(P.rbox(g, name, tone, cx, cy, hx, hy, f, z0, z1, k));
      const toChair = ([x, y]) => { const dx = CHAIR[0] - x, dy = CHAIR[1] - y, l = Math.hypot(dx, dy); return [dx / l, dy / l]; };

      // 显示器：靠北墙，屏幕正对椅子；底座在屏幕前面（人的这一侧）
      const M = MON, fm = toChair(M);
      rb("props", "monitor-stand", 0.6, M[0] + 3 * fm[0], M[1] + 3 * fm[1], 8, 6, fm, DESK, DESK + 3);
      rb("props", "monitor", 0.95, M[0], M[1], 24, 1.5, fm, DESK + 3, DESK + 45);

      // 小书架（带书）、花盆、小苗：22 宽、14 深，立在竖边上、背板靠东墙（x=-225 这一侧）、开口朝西（朝人）；北边的侧板高一点
      {
        const hx0 = -224, hx1 = -210, hy0 = 128, hy1 = 150, zTop = 104, zTall = 114.8;
        box("hutch", "hutch-back", 0.6, hx0, hx0 + 2, hy0 + 2.3, hy1 - 2.3, DESK, zTop);
        box("hutch", "hutch-side-n", 0.66, hx0, hx1, hy0, hy0 + 2.3, DESK, zTall);
        box("hutch", "hutch-side-s", 0.66, hx0, hx1, hy1 - 2.3, hy1, DESK, zTop);
        box("hutch", "hutch-board-top", 0.7, hx0 + 2, hx1, hy0 + 2.3, hy1 - 2.3, zTop - 2, zTop);
        [[2.6, 96.6], [6.2, 99.8], [9.8, 98.7], [13.4, 98]].forEach(([dy, zt], i) => box("hutch", `book-${i + 1}`, 0.55 + 0.1 * (i % 2), hx0 + 3, hx1 - 2, hy0 + dy, hy0 + dy + 3.5, DESK, zt));
        fru("hutch", "pot", 0.95, hx0 + 8, hx0 + 18, hy0 + 6, hy0 + 16, zTop, zTop + 10.6, 0.8);
        box("hutch", "sprout", 0.6, hx0 + 11.5, hx0 + 14.5, hy0 + 9.5, hy0 + 12.5, zTop + 10.6, zTop + 20.6);
      }

      // 书摞（下边最西头）：最底下是最西边那本（原来的报纸，现在按书的大小当底），上面是 5 本书、她写字的本子；
      // 状态 A 里合上的笔记本在最上面（状态 B 里它摊开在桌上，见下）
      {
        let z = DESK;
        const layer = (name, tone, w, d, h, dx, dy) => { box("props", name, tone, PILE[0] + dx - w / 2, PILE[0] + dx + w / 2, PILE[1] + dy - d / 2, PILE[1] + dy + d / 2, z, z + h); z += h; };
        layer("west-book", 0.85, 27, 22, 2.2, 0, 0);
        [[27.5, 22.5, 0.8], [26, 21.5, -0.6], [27, 22, 0.4], [26.5, 22.5, -0.9], [27, 21.5, 0.5]].forEach(([w, d, dx], i) => layer(`stack-${i + 1}`, 0.62 + 0.06 * (i % 2), w, d, 4.3, dx, i % 2 ? 0.5 : -0.5));
        layer("sketchbook", 0.9, 24, 15, 1.5, 1, -1);
        tag("A", () => layer("notebook-closed", 0.9, 18, 22, 2.4, -1, 1));
      }
      // 状态 B：摊开的笔记本——封面（比纸大一圈）+ 左页 + 右页，两页中间留一条书脊缝，纸从外沿往书脊微微下斜；
      // 每页 16.2×23.2（B5 左右），摊开 33×23；坐着的人写字用右页，右页中心在她正前方偏右约 8cm。
      // 笔记本自己的坐标：a 沿右手边、b 沿页面朝上（远离人）；朝向 NB_F（见上）
      tag("B", () => {
        const f = NB_F, r = [f[1], -f[0]];
        const W = (a, b) => [NOTEBOOK[0] + a * r[0] + b * f[0], NOTEBOOK[1] + a * r[1] + b * f[1]];
        // 顶面沿 a 方向从 zA（a0 那端）斜到 zB（a1 那端）的楔形
        const slab = (name, tone, a0, a1, b0, b1, z0, zA, zB) => {
          const c = [[a0, b0], [a1, b0], [a1, b1], [a0, b1]].map(([a, b]) => W(a, b));
          const v = c.map((q) => [q[0], q[1], z0]).concat(c.map((q, i) => [q[0], q[1], i === 0 || i === 3 ? zA : zB]));
          O.push(P.mesh("props", name, tone, v, [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]], c));
        };
        slab("notebook-cover", 0.35, -17.8, 17.8, -12.6, 12.6, DESK, DESK + 0.4, DESK + 0.4);
        slab("notebook-page-l", 0.92, -16.6, -0.4, -11.6, 11.6, DESK + 0.4, DESK + 1.6, DESK + 0.9);
        slab("notebook-page-r", 0.96, 0.4, 16.6, -11.6, 11.6, DESK + 0.4, DESK + 0.9, DESK + 1.6);
      });

      // 杯子：在写字区的东边（人的右手边），把手朝东
      prism("props", "mug", 0.4, -128, 80, 4.3, 4.3, DESK, DESK + 8.8, 10);
      box("props", "mug-handle", 0.4, -135.8, -132.3, 78.5, 81.5, DESK + 2.7, DESK + 8.4);

      // 竖边（东边那条桌面）：红色机器 + 香薰瓶靠近内角，玩偶在竖边南端
      box("props", "red-machine", 0.38, -208, -160, 98, 124, DESK, DESK + 29);
      prism("props", "diffuser", 0.45, -184, 111, 4, 4, DESK + 29, DESK + 38, 8);
      box("props", "plush", 0.82, -219, -209, 156, 166, DESK, DESK + 11.4);
    }

    /* ───────── 桌前的椅子：没有扶手；坐在 L 的凹角里，座面朝显示器（CHAIR_F：朝北偏东约 26°，画面里朝左前方）─────────
     * 座面完全在下边那条桌面的南面（y > 95，相机看过去就是桌子后面）和竖边的西面（x > -160），不塞在桌下、也不碰竖边。
     * 座面中心取 y=142：转了 26° 以后北角在 y≈108，落在桌沿（y=95）后面。 */
    {
      const f = CHAIR_F, C = CHAIR;
      const rot = (g, name, tone, a, b, hx, hy, z0, z1, k) => O.push(P.rbox(g, name, tone, C[0] + a * f[1] + b * f[0], C[1] - a * f[0] + b * f[1], hx, hy, f, z0, z1, k));
      // a：沿右手边；b：沿朝向（正数 = 朝显示器）
      prism("girlchair", "base", 0.45, C[0], C[1], 22, 22, 0, 6, 10);
      const up = 17;                                       // 椅子加高 17cm（座面 48 → 65）
      prism("girlchair", "post", 0.5, C[0], C[1], 3.5, 3.5, 6, 42 + up, 8);
      rot("girlchair", "seat", 0.5, 0, 0, 26, 25, 42 + up, 48 + up);
      rot("girlchair", "back", 0.72, 0, -30.5, 28, 5.5, 48 + up, 106 + up, 0.9);
    }

    /* ───────── 书架：原来的书架（书桌书架组合里的那个）改成单独的落地书架 ─────────
     * 靠东墙（x=-225）、正面朝西，在竖边南端（y=180）和沙发前沿（y=294）之间的墙边：这段墙 114cm，书架宽从 120 改成 104（两头各留 5），
     * 进深 40、高 225 不变；原来「第一块隔板和桌面同高」不再需要，4 层均分。书桌视角里它在最左边露出来。 */
    {
      const bx0 = xe, bx1 = xe + 40, by0 = 185, by1 = 289, sh = 225, base = 6, top = 4, bt = 3, side = 4;
      const row = (sh - base - top - 3 * bt) / 4;
      box("bookcase", "base", 0.7, bx0, bx1, by0, by1, 0, base);
      box("bookcase", "side-n", 0.78, bx0, bx1, by0, by0 + side, base, sh - top);
      box("bookcase", "side-s", 0.78, bx0, bx1, by1 - side, by1, base, sh - top);
      box("bookcase", "back", 0.7, bx0, bx0 + 1.5, by0 + side, by1 - side, base, sh - top);
      for (let i = 1; i <= 3; i++) box("bookcase", `board-${i}`, 0.8, bx0 + 1.5, bx1, by0 + side, by1 - side, base + row * i + bt * (i - 1), base + row * i + bt * i);
      box("bookcase", "top", 0.82, bx0, bx1, by0, by1, sh - top, sh);
    }

    /* ───────── 食物桌（v10）：贴北墙，在书桌西端（x 15）和衣柜（x 233）之间；放吃的，爸妈出差时她在这里一个人吃饭 ───────── */
    {
      const T = LAYOUT.FOODTABLE;
      box("foodtable", "top", 0.8, T.x0, T.x1, T.y0, T.y1, T.h - 3, T.h);
      [[T.x0 + 3, T.y0 + 3], [T.x1 - 7, T.y0 + 3], [T.x0 + 3, T.y1 - 7], [T.x1 - 7, T.y1 - 7]].forEach(([x, y], i) => box("foodtable", `leg-${i}`, 0.62, x, x + 4, y, y + 4, 0, T.h - 3));
      box("foodtable", "shelf", 0.7, T.x0 + 4, T.x1 - 4, T.y0 + 4, T.y1 - 4, 18, 20);           // 下面一层隔板
    }

    /* ───────── 衣柜：贴北墙（y=30）、西侧贴西墙（x=365），门朝南。脚 + 柜身 + 顶檐 + 两扇门板 ───────── */
    {
      const wx = xw - 2 - 130, ww = 130, wd = 56, yb = yn, yf = yn + wd;      // 柜身 x 233~363，y 30~86
      box("wardrobe", "feet", 0.66, wx + 3, wx + ww - 3, yb + 3, yf - 3, 0, 8);
      box("wardrobe", "body", 0.78, wx, wx + ww, yb, yf, 8, 202);
      box("wardrobe", "cornice", 0.72, wx - 2, wx + ww + 2, yb, yf + 2, 202, 210);
      box("wardrobe", "door-l", 0.86, wx + 3, wx + 63, yf, yf + 1.5, 14, 196);
      box("wardrobe", "door-r", 0.86, wx + 67, wx + 127, yf, yf + 1.5, 14, 196);
    }

    /* ───────── 床 + 床头柜：在西南角——床头靠西墙、长边靠南墙；床头柜在床头的北侧，门洞在床头柜北边的西墙上 ─────────
     * 床 x 154~359、y 234~354（床头在西端）；床头柜 x 319~359、y 188~230，抽屉朝东；西墙门洞（y 92~182）里侧 90×90 没有家具。 */
    {
      const bx1 = xw - 6, bx0 = bx1 - 205, by1 = ys - 6, bd = 120, by0 = by1 - bd, hb = 10;
      const mx1 = bx1 - hb, mx0 = mx1 - 191, my = by0 + 4, md = bd - 8;
      box("bed", "headboard", 0.7, bx1 - hb, bx1, by0, by1, 0, 85);
      box("bed", "frame", 0.78, bx0, bx1 - hb, by0, by1, 0, 26);
      box("bed", "mattress", 0.96, mx0, mx1, my, my + md, 26, 46);
      box("bed", "pillow-1", 0.99, mx1 - 35, mx1 - 5, my + 5, my + 53, 46, 56);
      box("bed", "pillow-2", 0.99, mx1 - 35, mx1 - 5, my + 59, my + 107, 46, 56);
      box("bed", "blanket", 0.88, mx0 + 2, mx1 - 60, my + 2, my + md - 2, 46, 52);

      // 床头柜：靠西墙、在床头北侧，离床 4cm。脚 + 柜身 + 台面 + 两个抽屉面（朝东）
      const nd = 40, nw = 42, nx = bx1 - nd, ny = by0 - 4 - nw;
      box("nightstand", "feet", 0.66, nx + 3, nx + nd - 3, ny + 3, ny + nw - 3, 0, 6);
      box("nightstand", "body", 0.78, nx, nx + nd, ny, ny + nw, 6, 46);
      box("nightstand", "top", 0.82, nx - 1.5, nx + nd, ny - 1.5, ny + nw + 1.5, 46, 50);
      box("nightstand", "drawer-1", 0.86, nx - 1.5, nx, ny + 3, ny + nw - 3, 10, 25);
      box("nightstand", "drawer-2", 0.86, nx - 1.5, nx, ny + 3, ny + nw - 3, 28, 43);
    }
    return O.filter((o) => !o.st || o.st === state);
  }

  // 房间页面要用的布局常量（书架 / 椅子 / 书桌的位置），和上面 buildScene 里的是同一份
  const LAYOUT = {
    DESK, CHAIR, CHAIR_F, MON, PILE, NOTEBOOK, NB_F,
    BOOKCASE: { x0: ROOM.xe, x1: ROOM.xe + 40, y0: 185, y1: 289, h: 225, base: 6, top: 4, bt: 3, side: 4, back: 1.5, rows: 4 },
    SOFA: { x0: ROOM.xe, x1: 35, y0: ROOM.ys - 66, y1: ROOM.ys, seat: 45, back: 84 },
    SIDEBOARD: { x0: 45, x1: 145, y0: ROOM.ys - 40, y1: ROOM.ys, h: 85 },
    BED: { x0: ROOM.xw - 6 - 205, x1: ROOM.xw - 6, y0: ROOM.ys - 6 - 120, y1: ROOM.ys - 6 },
    NIGHTSTAND: { x0: ROOM.xw - 6 - 40, x1: ROOM.xw - 6, y0: ROOM.ys - 6 - 120 - 4 - 42, y1: ROOM.ys - 6 - 120 - 4, top: 50 },
    WARDROBE: { x0: ROOM.xw - 2 - 130, x1: ROOM.xw - 2, y0: ROOM.yn, y1: ROOM.yn + 56, top: 210 },
    FOODTABLE: { x0: 60, x1: 200, y0: ROOM.yn, y1: ROOM.yn + 50, h: 75 },
  };
  const API = { GROUPS, ROOM, DOOR, WIN, DESK_YAW, DESK_X, STATES, DEFAULT_STATE, LAYOUT, buildScene };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.RoomScene3D = API;
})(typeof window !== "undefined" ? window : globalThis);
