"""Optional token budgets measured by a host-supplied, synchronous counter.

There is deliberately no characters-per-token estimate. A counter is only as
accurate as its handling of the target model's tokenizer and message template.
No model, tokenizer, provider SDK, or network service is loaded by this module.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable

from .models import clone


def _validate_counter(counter: object) -> None:
    if not callable(counter):
        raise TypeError("counter must be a synchronous callable.")
    for target in (counter, getattr(counter, "__call__", None)):
        if (inspect.iscoroutinefunction(target) or inspect.isasyncgenfunction(target)
                or inspect.isgeneratorfunction(target)):
            raise TypeError("counter must be synchronous and return a nonnegative integer.")


def _reject_deferred(value: object) -> None:
    if inspect.iscoroutine(value) or inspect.isgenerator(value):
        # Do not leave a newly returned coroutine unawaited, or start deferred
        # work merely to count tokens. Its body is never executed here.
        value.close()
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        raise TypeError("counter must return an integer, not deferred or asynchronous work.")


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """A hard limit measured over complete compiled messages.

    ``max_tokens`` is the total caller-declared window. Reserved output and
    externally accounted overhead are subtracted to give ``input_limit``.
    The counter must include the full framing required by the target model for
    this message list. Put request material outside that list (for example a
    tool schema) in ``overhead_tokens``; do not count the same framing twice.

    Counters receive fresh message copies and must synchronously return a
    nonnegative Python integer. Tokenization may run once per optional turn
    considered, plus the mandatory set and a final verification. Keep counters
    local, deterministic, and free of side effects.
    """

    max_tokens: int
    counter: Callable[[list[dict]], int] = field(repr=False)
    reserve_output_tokens: int = field(default=0, kw_only=True)
    overhead_tokens: int = field(default=0, kw_only=True)
    counter_name: str = field(default="custom", kw_only=True)

    def __post_init__(self) -> None:
        for name, minimum in (("max_tokens", 1), ("reserve_output_tokens", 0),
                              ("overhead_tokens", 0)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer of at least {minimum}.")
        if self.input_limit < 1:
            raise ValueError("Reserved output and overhead must leave at least one input token.")
        if type(self.counter_name) is not str or not self.counter_name.strip():
            raise ValueError("counter_name must be a nonempty string.")
        _validate_counter(self.counter)

    @property
    def input_limit(self) -> int:
        return self.max_tokens - self.reserve_output_tokens - self.overhead_tokens

    def count(self, messages: list[dict]) -> int:
        """Measure a defensive copy of the entire supplied message list."""
        if type(messages) is not list:
            raise TypeError("messages must be a list.")
        measured = self.counter(clone(messages))
        _reject_deferred(measured)
        if type(measured) is not int:
            raise TypeError("counter must return a nonnegative Python integer.")
        if measured < 0:
            raise ValueError("counter must return a nonnegative Python integer.")
        return measured


class TokenBudgetExceeded(ValueError):
    """Mandatory context, or the final exact check, exceeds the token limit."""

    def __init__(self, used_tokens: int, budget: TokenBudget):
        self.used_tokens = used_tokens
        self.max_tokens = budget.max_tokens
        self.input_limit = budget.input_limit
        self.reserve_output_tokens = budget.reserve_output_tokens
        self.overhead_tokens = budget.overhead_tokens
        super().__init__(
            f"Context needs {used_tokens} input tokens according to {budget.counter_name!r}; "
            f"input limit is {budget.input_limit} ({budget.max_tokens} total, "
            f"{budget.reserve_output_tokens} reserved output, {budget.overhead_tokens} overhead)."
        )


def make_hf_token_counter(tokenizer: object) -> Callable[[list[dict]], int]:
    """Use an already loaded Hugging Face compatible tokenizer's chat template.

    The tokenizer must support ``apply_chat_template(messages, tokenize=True,
    add_generation_prompt=True, return_dict=False)`` and return a flat list of
    token IDs. The host loads and configures the tokenizer; this helper never
    imports Transformers or downloads a model. Template errors propagate. A
    local template's count is not a promise about a remote provider's framing.
    """
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    _validate_counter(apply_template)

    def count(messages: list[dict]) -> int:
        token_ids = apply_template(
            clone(messages), tokenize=True, add_generation_prompt=True, return_dict=False
        )
        _reject_deferred(token_ids)
        if type(token_ids) is not list or any(type(token) is not int or token < 0 for token in token_ids):
            raise TypeError("The tokenizer chat template must return a flat list of nonnegative integer token IDs.")
        return len(token_ids)

    return count
