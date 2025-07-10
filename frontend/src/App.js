import React, { useState } from 'react';
import axios from 'axios';

function parseBotResponse(response) {
  // Extract all block changes
  const blockPattern = /File:\s*(.*?)\s*Block:\s*(.*?)\s*```hcl\s*([\s\S]*?)```/g;
  let match;
  const blocks = [];
  while ((match = blockPattern.exec(response)) !== null) {
    blocks.push({
      file: match[1].trim(),
      block: match[2].trim(),
      code: match[3].trim(),
    });
  }

  // Optionally extract a summary
  let summary = null;
  const summaryMatch = response.match(/Summary:(.*?)(File:|$)/s);
  if (summaryMatch) {
    summary = summaryMatch[1].replace(/\n/g, ' ').trim();
  }

  return { summary, blocks };
}

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [awaitingApproval, setAwaitingApproval] = useState(false);

  const user_id = 'demo';

  const sendMessage = async () => {
    if (!input.trim()) return;
    setMessages([...messages, { sender: 'user', text: input }]);
    const res = await axios.post('/chat', { message: input, user_id });
    const { summary, blocks } = parseBotResponse(res.data.response);
    let summaryText = summary;
    if ((blocks && blocks.length > 0)) {
      // Always get a fresh summary from backend
      try {
        const sumRes = await axios.post('/summarize', { user_id });
        summaryText = sumRes.data.summary;
      } catch (e) {
        summaryText = summary || '';
      }
    }
    if (summaryText || (blocks && blocks.length > 0)) {
      setMessages(msgs => [
        ...msgs,
        { sender: 'bot', summary: summaryText, blocks, text: res.data.response, showApproval: true }
      ]);
      setAwaitingApproval(true);
    } else {
      setMessages(msgs => [...msgs, { sender: 'bot', text: res.data.response }]);
    }
    setInput('');
  };

  const handleApproval = async (action) => {
    setAwaitingApproval(false);
    const res = await axios.post('/approve', { user_id, action });
    setMessages(msgs => [
      ...msgs,
      { sender: 'bot', text: res.data.result }
    ]);
  };

  return (
    <div style={{ maxWidth: 600, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h2>GCP Terraform Chatbot</h2>
      <div style={{ border: '1px solid #ccc', minHeight: 200, padding: 10, marginBottom: 10 }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ textAlign: msg.sender === 'user' ? 'right' : 'left', marginBottom: 16 }}>
            <b>{msg.sender === 'user' ? 'You' : 'Bot'}:</b>
            {msg.sender === 'bot' && (msg.summary || (msg.blocks && msg.blocks.length > 0)) ? (
              <div>
                {msg.summary && <div style={{ margin: '8px 0', color: '#333' }}><b>Summary:</b> {msg.summary}</div>}
                {msg.blocks && msg.blocks.map((block, idx) => (
                  <div key={idx} style={{ margin: '8px 0' }}>
                    <b>File:</b> {block.file} <b>Block:</b> {block.block}
                    <pre style={{ background: '#f4f4f4', padding: 10, borderRadius: 4, overflowX: 'auto' }}>
                      {block.code}
                    </pre>
                  </div>
                ))}
                {msg.showApproval && i === messages.length - 1 && awaitingApproval && (
                  <div style={{ marginTop: 10 }}>
                    <button onClick={() => handleApproval('approve')} style={{ marginRight: 8, padding: '6px 16px', background: '#4caf50', color: 'white', border: 'none', borderRadius: 4 }}>Create New Branch</button>
                    <button onClick={() => handleApproval('reject')} style={{ padding: '6px 16px', background: '#f44336', color: 'white', border: 'none', borderRadius: 4 }}>Reject</button>
                  </div>
                )}
              </div>
            ) : (
              <span> {msg.text}</span>
            )}
          </div>
        ))}
      </div>
      <input
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && sendMessage()}
        style={{ width: '80%', padding: 8 }}
        placeholder="Type your command..."
      />
      <button onClick={sendMessage} style={{ padding: 8, marginLeft: 8 }}>Send</button>
    </div>
  );
}

export default App; 