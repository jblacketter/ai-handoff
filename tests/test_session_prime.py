"""Priming readiness: wait for the agent's input prompt before sending
PRIME_MESSAGE, instead of a fixed sleep that Claude Code's startup outran."""

from unittest.mock import call, patch

from tagteam.session import (
    AGENT_READY_TAIL_LINES,
    PRIME_MESSAGE,
    SESSION_NAME,
    agent_prompt_visible,
    create_tmux_session,
    wait_for_agent_ready,
)


class TestAgentPromptVisible:
    def test_claude_code_prompt(self):
        assert agent_prompt_visible("banner\n\n❯ \n\n  ⏵⏵ auto mode on (shift+tab to cycle)")

    def test_codex_prompt(self):
        assert agent_prompt_visible("› Ask Codex\n  gpt-5.6 · /model to change")

    def test_shell_prompt_is_not_ready(self):
        # watcher.IDLE_PATTERNS accepts these; priming must not.
        assert not agent_prompt_visible("jack@mac tagteam % ")
        assert not agent_prompt_visible("$ claude\n")

    def test_empty_and_booting(self):
        assert not agent_prompt_visible("")
        assert not agent_prompt_visible("   \n  ")
        assert not agent_prompt_visible("Loading MCP servers...")

    def test_only_tail_is_considered(self):
        # A prompt scrolled far up (e.g. previous agent run) does not count.
        old_prompt = "❯ old\n" + "\n".join(f"line {i}" for i in range(AGENT_READY_TAIL_LINES + 2))
        assert not agent_prompt_visible(old_prompt)


class TestWaitForAgentReady:
    @patch("tagteam.session.time.sleep")
    def test_returns_true_when_prompt_appears(self, mock_sleep):
        polls = iter(["", "booting", "❯ "])
        assert wait_for_agent_ready(lambda: next(polls), label="lead", timeout=60) is True
        # Two not-ready polls -> two poll sleeps, plus one settle sleep.
        assert mock_sleep.call_count == 3

    @patch("tagteam.session.time.monotonic")
    @patch("tagteam.session.time.sleep")
    def test_times_out_and_warns_with_prime_text(self, mock_sleep, mock_monotonic, capsys):
        mock_monotonic.side_effect = [0.0, 0.1, 0.2, 999.0]
        assert wait_for_agent_ready(lambda: "still booting", label="lead", timeout=5) is False
        out = capsys.readouterr().out
        assert "lead prompt not detected" in out
        assert PRIME_MESSAGE in out


class TestTmuxPriming:
    @patch("tagteam.session.time.sleep")
    @patch("tagteam.watcher.capture_pane")
    @patch("tagteam.session._tmux")
    @patch("tagteam.session.session_exists", return_value=False)
    @patch("tagteam.session._tmux_supported", return_value=True)
    def test_primes_each_pane_after_its_prompt(
        self, mock_supported, mock_exists, mock_tmux, mock_capture, mock_sleep, tmp_path
    ):
        (tmp_path / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"
        )
        lead_polls = iter(["% claude", "❯ "])

        def capture(pane, last_n_lines=5):
            if pane == f"{SESSION_NAME}:0.0":
                return next(lead_polls)
            return "› "

        mock_capture.side_effect = capture

        assert create_tmux_session(str(tmp_path), launch=True) is True

        sends = [c for c in mock_tmux.call_args_list if c[0][0] == "send-keys"]
        assert sends[-2] == call("send-keys", "-t", f"{SESSION_NAME}:0.0", PRIME_MESSAGE, "Enter")
        assert sends[-1] == call("send-keys", "-t", f"{SESSION_NAME}:0.2", PRIME_MESSAGE, "Enter")
        lead_captures = [c for c in mock_capture.call_args_list if c[0][0] == f"{SESSION_NAME}:0.0"]
        assert len(lead_captures) == 2
