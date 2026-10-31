"""回复格式：把一次回复拆成多条短消息，像真人发微信一样。

模型按约定用「单独一行 %%」分隔每一条短消息，
后端在流式过程中就把它们切开，每条各存一行、各占一个气泡。

* 流式安全：只有完整的一行才会被判定是不是分隔符，
  所以不会把半个 `%%` 误当成正文吐出去，也不会漏掉分隔符。
* 没写分隔符就原样一条，不会硬拆。
"""

from __future__ import annotations

SPLIT_DELIMITER = "%%"


def format_block() -> str:
    """注入给模型的格式要求。

    主要以「空行」为界：模型本来就这么写（人设里也常写「多用换行」），
    顺着它的习惯比硬塞一个自定义分隔符可靠得多。`%%` 仍然认，作为备选。
    """
    return (
        "【回复格式】像真人发微信那样，把要说的话拆成一条条短消息：\n"
        "- 每条之间**空一行**（连按两下回车）。系统会把空行切成独立的聊天气泡。\n"
        "- 每条尽量短：通常一句，最多两句；别写成一段小作文。\n"
        "- 一次回复一般 1~4 条。话少就只发一条，不要为了凑条数硬拆。\n"
        "- 每条都要能独立看懂，别把一个句子劈成两半。\n"
        "例如：\n"
        "今天几号？\n"
        "\n"
        "9 月 13 号，星期日。\n"
        "\n"
        "你上周说的那份稿子，写完了吗"
    )


class ReplySplitter:
    """把流式输出切成多条短消息。

    切分依据（满足任一即为一条的边界）：
      * **空行**——模型和人设都习惯用「空行分段」，这是主力规则；
      * **单独一行 `%%`**——显式分隔符，作为备选（模型愿意用就用）。

    同一段内部的单个换行不算边界，所以「一句话换行接着写」不会被劈开。

    用法::

        sp = ReplySplitter()
        for kind, chunk in sp.feed(delta_text):
            ...            # kind == "text" 追加到当前气泡；"break" 表示当前这条结束
        for kind, chunk in sp.flush():
            ...
    """

    def __init__(self, delimiter: str = SPLIT_DELIMITER) -> None:
        self.delimiter = delimiter
        self.line = ""         # 当前这一行的完整内容（包含已经发出去的部分）
        self.sent = 0          # 这一行里已经发给前端的字符数
        self.parts = 0         # 已经切出来的条数（不含正在写的这条）

    def _is_boundary(self, line: str) -> bool:
        stripped = line.strip()
        return stripped == "" or stripped == self.delimiter

    def feed(self, text: str) -> list[tuple[str, str]]:
        """喂一段增量，返回一串 ("text", 内容) / ("break", "") 动作。"""
        actions: list[tuple[str, str]] = []
        buffered = ""

        segments = text.split("\n")
        for seg in segments[:-1]:
            # 除最后一段外，每一段都凑满了一整行
            line = self.line + seg
            sent = self.sent
            self.line, self.sent = "", 0
            if self._is_boundary(line):
                if buffered:
                    actions.append(("text", buffered))
                    buffered = ""
                actions.append(("break", ""))
                self.parts += 1
            else:
                buffered += line[sent:] + "\n"

        # 最后一段是当前这一行的未完部分。只有「整行还有可能变成边界」时才扣住
        # ——也就是纯空白，或者分隔符的前缀（比如半个 `%`）。
        self.line += segments[-1]
        core = self.line.strip()
        if core and not self.delimiter.startswith(core):
            buffered += self.line[self.sent :]
            self.sent = len(self.line)

        if buffered:
            actions.append(("text", buffered))
        return actions

    def flush(self) -> list[tuple[str, str]]:
        """流结束时把尾巴处理掉。"""
        line, sent = self.line, self.sent
        self.line, self.sent = "", 0
        if not line:
            return []
        if self._is_boundary(line):
            if line.strip():
                self.parts += 1
                return [("break", "")]
            return []
        rest = line[sent:]
        return [("text", rest)] if rest else []
