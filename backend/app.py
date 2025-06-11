import sqlite3
import uuid
import re
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
import os
import sys
from io import StringIO
import pickle
import base64
import nltk
from nltk.tokenize import word_tokenize
from collections import Counter
import plotly.graph_objects as go
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from typing import Dict, List, TypedDict, Annotated, Sequence
import logging
from langchain_core.tools import tool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Download NLTK data
nltk.download('punkt')
nltk.download('punkt_tab')

# SQLite Database Setup
def init_db():
    try:
        with sqlite3.connect('candidates.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scraped_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT,
                    content TEXT,
                    summary TEXT,
                    translated TEXT,
                    language TEXT,
                    word_freq_data TEXT,
                    graph_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        socketio.emit('error', {'message': f'Database initialization failed: {str(e)}'})

init_db()

# LLM Configuration
API_KEY = ""
try:
    llm = ChatGroq(model="llama3-70b-8192", api_key=API_KEY)
    logger.info("LLM initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize LLM: {e}")
    socketio.emit('error', {'message': f'LLM initialization failed: {str(e)}'})
    llm = None

# Define LangGraph State
class AgentState(TypedDict):
    messages: Annotated[Sequence[HumanMessage], "The messages in the conversation"]
    content: str
    summary: str
    translated: str
    language: str
    word_freq_data: str
    batch_id: str
    current_variables: Dict
    intermediate_outputs: List[Dict]
    output_image_paths: List[str]
    graph_url: str

# Python REPL Tool
persistent_vars = {}
plotly_saving_code = """import pickle
import uuid
import plotly
import os

if not os.path.exists("images/plotly_figures/pickle"):
    os.makedirs("images/plotly_figures/pickle")

for figure in plotly_figures:
    pickle_filename = f"images/plotly_figures/pickle/{uuid.uuid4()}.pickle"
    with open(pickle_filename, 'wb') as f:
        pickle.dump(figure, f)
"""

@tool(parse_docstring=True)
def complete_python_task(
    graph_state: Annotated[dict, "InjectedState"],
    thought: str,
    python_code: str
) -> tuple[str, dict]:
    """Completes a python task

    Args:
        thought: Internal thought about the next action to be taken, and the reasoning behind it. This should be formatted in MARKDOWN and be high quality.
        python_code: Python code to be executed to perform analyses, create a new dataset or create a visualization.
    """
    current_variables = graph_state.get("current_variables", {})
    current_image_pickle_files = os.listdir("images/plotly_figures/pickle") if os.path.exists("images/plotly_figures/pickle") else []
    
    try:
        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        # Execute the code and capture the result
        exec_globals = globals().copy()
        exec_globals.update(persistent_vars)
        exec_globals.update(current_variables)
        exec_globals.update({"plotly_figures": []})

        exec(python_code, exec_globals)
        persistent_vars.update({k: v for k, v in exec_globals.items() if k not in globals()})

        # Get the captured stdout
        output = sys.stdout.getvalue()

        # Restore stdout
        sys.stdout = old_stdout

        updated_state = {
            "intermediate_outputs": [{"thought": thought, "code": python_code, "output": output}],
            "current_variables": persistent_vars
        }

        if 'plotly_figures' in exec_globals:
            exec(plotly_saving_code, exec_globals)
            new_image_folder_contents = os.listdir("images/plotly_figures/pickle")
            new_image_files = [file for file in new_image_folder_contents if file not in current_image_pickle_files]
            if new_image_files:
                updated_state["output_image_paths"] = new_image_files
            
            persistent_vars["plotly_figures"] = []

        return output, updated_state
    except Exception as e:
        return str(e), {"intermediate_outputs": [{"thought": thought, "code": python_code, "output": str(e)}]}

# Base Agent class
class Agent:
    def __init__(self, name):
        self.name = name

    def emit_thought(self, message):
        logger.info(f"{self.name}: {message}")
        socketio.emit('thought', {'agent': self.name, 'message': message})

# Summarizer Agent
class SummarizerAgent(Agent):
    def __init__(self):
        super().__init__("Summarizer")

    def summarize(self, state: AgentState) -> AgentState:
        try:
            self.emit_thought("Summarizing content...")
            content = state["content"]
            prompt = f"""Summarize the following content in 150 words or less, capturing the main ideas without using * in response:

            Content:
            {content}

            Provide a concise summary."""
            response = llm.invoke(prompt)
            self.emit_thought("Summarization complete")
            return {"summary": response.content}
        except Exception as e:
            self.emit_thought(f"Error summarizing content: {str(e)}")
            socketio.emit('error', {'message': f'Summarization failed: {str(e)}'})
            return {"summary": f"Error: {str(e)}"}

# Translator Agent
class TranslatorAgent(Agent):
    def __init__(self):
        super().__init__("Translator")

    def translate(self, state: AgentState) -> AgentState:
        try:
            self.emit_thought(f"Translating summary to {state['language']}...")
            summary = state["summary"]
            language = state["language"]
            language_map = {
                'en': 'English',
                'es': 'Spanish',
                'fr': 'French',
                'de': 'German',
                'hi': 'Hindi'
            }
            target_language = language_map.get(language, 'English')
            prompt = f"""Translate the following text to {target_language} without using * in response:

            Text:
            {summary}

            Provide the translated text."""
            response = llm.invoke(prompt)
            self.emit_thought("Translation complete")
            return {"translated": response.content}
        except Exception as e:
            self.emit_thought(f"Error translating content: {str(e)}")
            socketio.emit('error', {'message': f'Translation failed: {str(e)}'})
            return {"translated": f"Error: {str(e)}"}

# Visualization Agent
class VisualizationAgent(Agent):
    def __init__(self):
        super().__init__("Visualizer")

    def visualize(self, state: AgentState) -> AgentState:
        try:
            self.emit_thought("Generating word frequency visualization with Plotly...")
            content = state["content"]
            tokens = word_tokenize(content.lower())
            tokens = [t for t in tokens if t.isalnum()]
            word_freq = Counter(tokens).most_common(10)
            words, freqs = zip(*word_freq)

            # Python code for Plotly visualization
            python_code = """
import plotly.graph_objects as go
fig = go.Figure(data=[
    go.Bar(x=list(words), y=list(freqs), marker_color='orange')
])
fig.update_layout(
    title='Top 10 Word Frequencies',
    xaxis_title='Words',
    yaxis_title='Frequency',
    xaxis_tickangle=45
)
plotly_figures.append(fig)
"""
            thought = """## Visualization Plan
The visualization agent will create a bar chart of the top 10 most frequent words in the scraped content using Plotly. The chart will display words on the x-axis and their frequencies on the y-axis, with a title and rotated x-axis labels for readability. The figure will be appended to `plotly_figures` for saving."""

            # Execute the visualization code using the tool
            output, updated_state = complete_python_task(
                {
                    "current_variables": {"words": words, "freqs": freqs},
                    "input_data": []
                },
                thought,
                python_code
            )

            # Convert the latest Plotly figure to base64 for frontend display
            graph_url = None
            if updated_state.get("output_image_paths"):
                latest_pickle = max(
                    [os.path.join("images/plotly_figures/pickle", f) for f in updated_state["output_image_paths"]],
                    key=os.path.getctime
                )
                with open(latest_pickle, 'rb') as f:
                    fig = pickle.load(f)
                img_bytes = fig.to_image(format="png")
                graph_url = f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"

            self.emit_thought("Visualization complete")
            return {
                "word_freq_data": str(dict(word_freq)),
                "current_variables": updated_state.get("current_variables", {}),
                "intermediate_outputs": updated_state.get("intermediate_outputs", []),
                "output_image_paths": updated_state.get("output_image_paths", []),
                "graph_url": graph_url
            }
        except Exception as e:
            self.emit_thought(f"Error generating visualization: {str(e)}")
            socketio.emit('error', {'message': f'Visualization failed: {str(e)}'})
            return {"word_freq_data": f"Error: {str(e)}", "graph_url": None}

# Supervisor Agent
def supervisor_agent(state: AgentState) -> AgentState:
    agent = Agent("Supervisor")
    agent.emit_thought("Starting content processing pipeline...")

    if not state.get("summary"):
        agent.emit_thought("Routing to Summarizer")
        return {"next": "summarizer"}
    if not state.get("translated"):
        agent.emit_thought("Routing to Translator")
        return {"next": "translator"}
    if not state.get("word_freq_data"):
        agent.emit_thought("Routing to Visualizer")
        return {"next": "visualizer"}

    agent.emit_thought("Processing complete, storing results")
    try:
        with sqlite3.connect('candidates.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO scraped_data (batch_id, content, summary, translated, language, word_freq_data, graph_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                state["batch_id"],
                state["content"],
                state["summary"],
                state["translated"],
                state["language"],
                state["word_freq_data"],
                state["graph_url"]
            ))
            conn.commit()
            logger.info(f"Stored data for batch {state['batch_id']}")
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        socketio.emit('error', {'message': f'Database error: {str(e)}'})

    socketio.emit('result', {
        'batch_id': state["batch_id"],
        'summary': state["summary"],
        'translated': state["translated"],
        'word_freq_data': state["word_freq_data"],
        'graph_url': state.get("graph_url")
    })
    return {"next": END}

# Build LangGraph
workflow = StateGraph(AgentState)
workflow.add_node("supervisor", supervisor_agent)
workflow.add_node("summarizer", SummarizerAgent().summarize)
workflow.add_node("translator", TranslatorAgent().translate)
workflow.add_node("visualizer", VisualizationAgent().visualize)

workflow.set_entry_point("supervisor")
workflow.add_conditional_edges(
    "supervisor",
    lambda state: state["next"],
    {
        "summarizer": "summarizer",
        "translator": "translator",
        "visualizer": "visualizer",
        END: END
    }
)
workflow.add_edge("summarizer", "supervisor")
workflow.add_edge("translator", "supervisor")
workflow.add_edge("visualizer", "supervisor")

super_graph = workflow.compile()

# Flask Route
@app.route('/process', methods=['POST'])
def process_content():
    try:
        data = request.get_json()
        if not data or not data.get('content') or not data.get('language'):
            logger.error("Missing content or language")
            return jsonify({'error': 'Content and language are required'}), 400

        content = data['content']
        language = data['language']
        batch_id = str(uuid.uuid4())

        try:
            with sqlite3.connect('candidates.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO batches (batch_id)
                    VALUES (?)
                ''', (batch_id,))
                conn.commit()
                logger.info(f"Created batch {batch_id}")
        except sqlite3.Error as e:
            logger.error(f"Failed to create batch: {e}")
            socketio.emit('error', {'message': f'Failed to create batch: {str(e)}'})
            return jsonify({'error': f'Failed to create batch: {str(e)}'}), 500

        initial_state = {
            "messages": [HumanMessage(content="Process scraped content")],
            "content": content,
            "summary": None,
            "translated": None,
            "language": language,
            "word_freq_data": None,
            "batch_id": batch_id,
            "current_variables": {},
            "intermediate_outputs": [],
            "output_image_paths": [],
            "graph_url": None
        }

        for s in super_graph.stream(initial_state, {"recursion_limit": 150}):
            logger.info(f"Graph step: {s}")

        with sqlite3.connect('candidates.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT summary, translated, word_freq_data, graph_url
                FROM scraped_data
                WHERE batch_id = ?
            ''', (batch_id,))
            result = cursor.fetchone()
            if result:
                summary, translated, word_freq_data, graph_url = result
                return jsonify({
                    'summary': summary,
                    'translated': translated,
                    'word_freq_data': eval(word_freq_data) if word_freq_data and not word_freq_data.startswith("Error") else {},
                    'graph_url': graph_url,
                    'batch_id': batch_id
                }), 200
            else:
                return jsonify({'error': 'Processing incomplete'}), 500

    except Exception as e:
        logger.error(f"Processing error: {e}")
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)