from typing import Optional, NamedTuple
from dataclasses import dataclass, field
import asyncio
from concurrent.futures import ThreadPoolExecutor

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.messages import HumanMessage, SystemMessage

from .llm import get_llm


@dataclass
class TokenUsage:
    """Token usage statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    def add(self, other: "TokenUsage") -> "TokenUsage":
        """Add another TokenUsage to this one."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        return self
    
    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ProcessingResult:
    """Result from text processing with token usage."""
    text: str
    token_usage: TokenUsage
    chunk_count: int = 1
    
    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "token_usage": self.token_usage.to_dict(),
            "chunk_count": self.chunk_count,
        }


def _extract_token_usage(response) -> TokenUsage:
    """Extract token usage from LLM response."""
    usage = TokenUsage()
    
    # Try to get usage metadata from response
    if hasattr(response, 'usage_metadata') and response.usage_metadata:
        metadata = response.usage_metadata
        usage.input_tokens = getattr(metadata, 'input_tokens', 0) or metadata.get('input_tokens', 0) if isinstance(metadata, dict) else 0
        usage.output_tokens = getattr(metadata, 'output_tokens', 0) or metadata.get('output_tokens', 0) if isinstance(metadata, dict) else 0
        usage.total_tokens = getattr(metadata, 'total_tokens', 0) or metadata.get('total_tokens', 0) if isinstance(metadata, dict) else 0
        
        # If total not provided, calculate it
        if usage.total_tokens == 0:
            usage.total_tokens = usage.input_tokens + usage.output_tokens
    
    # Fallback: try response_metadata
    elif hasattr(response, 'response_metadata') and response.response_metadata:
        metadata = response.response_metadata
        if 'usage_metadata' in metadata:
            um = metadata['usage_metadata']
            usage.input_tokens = um.get('input_tokens', 0) or um.get('prompt_tokens', 0)
            usage.output_tokens = um.get('output_tokens', 0) or um.get('completion_tokens', 0)
            usage.total_tokens = um.get('total_tokens', usage.input_tokens + usage.output_tokens)
        elif 'token_usage' in metadata:
            tu = metadata['token_usage']
            usage.input_tokens = tu.get('prompt_tokens', 0)
            usage.output_tokens = tu.get('completion_tokens', 0)
            usage.total_tokens = tu.get('total_tokens', usage.input_tokens + usage.output_tokens)
    
    return usage


def process_text_with_instruction(
    text: str,
    instruction: str,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    merge_instruction: Optional[str] = None,
) -> ProcessingResult:
    """
    Process text by splitting into chunks, applying instruction to each chunk via LLM,
    and merging the results.
    
    Args:
        text: The input text to process.
        instruction: The instruction to apply to each chunk.
        chunk_size: Maximum size of each text chunk (default: 2000).
        chunk_overlap: Overlap between consecutive chunks (default: 200).
        merge_instruction: Optional instruction for merging results. 
                          If None, uses a default merge prompt.
    
    Returns:
        ProcessingResult containing the merged text and token usage statistics.
    """
    # Initialize text splitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    
    # Split text into chunks
    chunks = text_splitter.split_text(text)
    
    if not chunks:
        return ProcessingResult(text="", token_usage=TokenUsage(), chunk_count=0)
    
    # If only one chunk, process directly without merging
    if len(chunks) == 1:
        result_text, usage = _process_single_chunk(chunks[0], instruction)
        return ProcessingResult(text=result_text, token_usage=usage, chunk_count=1)
    
    # Process each chunk with the LLM
    llm = get_llm()
    chunk_results = []
    total_usage = TokenUsage()
    
    for i, chunk in enumerate(chunks, 1):
        chunk_prompt = f"""
{instruction}

--- TEXT CHUNK {i} of {len(chunks)} ---
{chunk}
--- END OF CHUNK ---

Process the above text chunk according to the instruction.
"""
        messages = [
            SystemMessage(content="You are a helpful assistant that processes text according to given instructions. Be concise and focused."),
            HumanMessage(content=chunk_prompt),
        ]
        
        response = llm.invoke(messages)
        chunk_results.append(_extract_content(response.content))
        total_usage.add(_extract_token_usage(response))
    
    # Merge all chunk results
    merged_text, merge_usage = _merge_chunk_results(chunk_results, instruction, merge_instruction)
    total_usage.add(merge_usage)
    
    return ProcessingResult(text=merged_text, token_usage=total_usage, chunk_count=len(chunks))


def _extract_content(content) -> str:
    """Extract string content from LLM response."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Handle list of content blocks
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
        )
    return str(content)


def _process_single_chunk(chunk: str, instruction: str) -> tuple[str, TokenUsage]:
    """Process a single chunk with the LLM."""
    llm = get_llm()
    
    messages = [
        SystemMessage(content="You are a helpful assistant that processes text according to given instructions."),
        HumanMessage(content=f"{instruction}\n\n{chunk}"),
    ]
    
    response = llm.invoke(messages)
    usage = _extract_token_usage(response)
    return _extract_content(response.content), usage


def _merge_chunk_results(
    chunk_results: list[str],
    original_instruction: str,
    merge_instruction: Optional[str] = None,
) -> tuple[str, TokenUsage]:
    """Merge results from multiple chunks into a final coherent output."""
    llm = get_llm()
    
    # Build the merge prompt
    if merge_instruction is None:
        merge_instruction = f"""
You were given the following instruction to apply to a long text that was split into chunks:
"{original_instruction}"

Below are the results from processing each chunk. Please merge these results into a single, 
coherent, and well-organized final output. Remove any redundancy and ensure smooth transitions.
"""
    
    # Compile all chunk results
    results_text = "\n\n".join([
        f"--- RESULT FROM CHUNK {i+1} ---\n{result}\n--- END ---"
        for i, result in enumerate(chunk_results)
    ])
    
    merge_prompt = f"""
{merge_instruction}

{results_text}

Please provide the merged final output:
"""
    
    messages = [
        SystemMessage(content="You are a helpful assistant that merges text segments into coherent outputs."),
        HumanMessage(content=merge_prompt),
    ]
    
    response = llm.invoke(messages)
    usage = _extract_token_usage(response)
    return _extract_content(response.content), usage


async def process_text_with_instruction_async(
    text: str,
    instruction: str,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    merge_instruction: Optional[str] = None,
    max_concurrent: int = 5,
) -> ProcessingResult:
    """
    Async version that processes chunks concurrently for better performance.
    
    Args:
        text: The input text to process.
        instruction: The instruction to apply to each chunk.
        chunk_size: Maximum size of each text chunk (default: 2000).
        chunk_overlap: Overlap between consecutive chunks (default: 200).
        merge_instruction: Optional instruction for merging results.
        max_concurrent: Maximum number of concurrent LLM calls (default: 5).
    
    Returns:
        ProcessingResult containing the merged text and token usage statistics.
    """
    # Initialize text splitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    
    # Split text into chunks
    chunks = text_splitter.split_text(text)
    
    if not chunks:
        return ProcessingResult(text="", token_usage=TokenUsage(), chunk_count=0)
    
    # If only one chunk, process directly
    if len(chunks) == 1:
        result_text, usage = _process_single_chunk(chunks[0], instruction)
        return ProcessingResult(text=result_text, token_usage=usage, chunk_count=1)
    
    llm = get_llm()
    chunk_usages: list[TokenUsage] = []
    
    async def process_chunk(chunk: str, index: int, total: int) -> tuple[str, TokenUsage]:
        chunk_prompt = f"""
{instruction}

--- TEXT CHUNK {index} of {total} ---
{chunk}
--- END OF CHUNK ---

Process the above text chunk according to the instruction.
"""
        messages = [
            SystemMessage(content="You are a helpful assistant that processes text according to given instructions. Be concise and focused."),
            HumanMessage(content=chunk_prompt),
        ]
        
        response = await llm.ainvoke(messages)
        usage = _extract_token_usage(response)
        return _extract_content(response.content), usage
    
    # Process chunks concurrently with semaphore to limit concurrent calls
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_with_semaphore(chunk: str, index: int, total: int) -> tuple[str, TokenUsage]:
        async with semaphore:
            return await process_chunk(chunk, index, total)
    
    # Create tasks for all chunks
    tasks = [
        process_with_semaphore(chunk, i + 1, len(chunks))
        for i, chunk in enumerate(chunks)
    ]
    
    # Execute all tasks concurrently
    results = await asyncio.gather(*tasks)
    
    # Separate results and usages
    chunk_results = [r[0] for r in results]
    chunk_usages = [r[1] for r in results]
    
    # Calculate total usage from chunks
    total_usage = TokenUsage()
    for usage in chunk_usages:
        total_usage.add(usage)
    
    # Merge results
    merged_text, merge_usage = _merge_chunk_results(list(chunk_results), instruction, merge_instruction)
    total_usage.add(merge_usage)
    
    return ProcessingResult(text=merged_text, token_usage=total_usage, chunk_count=len(chunks))

