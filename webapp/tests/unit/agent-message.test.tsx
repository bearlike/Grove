import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// Closed disclosures never mount this body. Real markdown is checked in-browser;
// its CSS import goes through Next's PostCSS setup, not the node test runner.
vi.mock("@/components/assistant-ui/markdown-text", () => ({ MarkdownText: () => null }));
import { AgentMessage } from "@/components/grove/workspace/agent-message";
import { outgoingAgentMessage } from "@/lib/grove/adapters/agent-message";
import type { ToolCallView } from "@/lib/grove/adapters/tool-call";

const call = (name: string, input: ToolCallView["input"]): ToolCallView => ({name,input,tool_use_id:"mail-1",status:"ok",result:null,duration_ms:10});

describe("mailbox protocol presentation", () => {
  it("reads current and legacy Claude message fields", () => {
    expect(outgoingAgentMessage(call("SendMessage",{to:"reviewer",message:"Full body",summary:"Review findings"}))).toMatchObject({from:"This session",to:"reviewer",subject:"Review findings",body:"Full body"});
    expect(outgoingAgentMessage(call("SendMessage",{recipient:"reviewer",content:"Older body"}))).toMatchObject({to:"reviewer",body:"Older body"});
  });
  it("recognizes the Grove mailbox without matching arbitrary tools by substring", () => {
    expect(outgoingAgentMessage(call("mcp__grove__grove_send_workspace_message",{workspace_id:"workspace-2",text:"Hello"}))).toMatchObject({to:"workspace-2",body:"Hello"});
    expect(outgoingAgentMessage(call("custom_SendMessage",{to:"x",message:"not mail"}))).toBeNull();
  });
  it("leaves subscription-only and control payloads in their native tool form", () => {
    expect(outgoingAgentMessage(call("SendMessage",{to:"x",notify_when_idle:true}))).toBeNull();
    expect(outgoingAgentMessage(call("SendMessage",{to:"x",message:{type:"shutdown_request"}}))).toBeNull();
  });
  it("does not claim delivery from a successful call alone", () => {
    expect(outgoingAgentMessage(call("SendMessage",{to:"x",message:"hi"}))?.state).toBe("Send call completed");
  });
  it("collapses even a subjectless one-line message and keeps the endpoints visible", () => {
    const html=renderToStaticMarkup(<AgentMessage message={{from:"reviewer",to:"lead",subject:null,body:"private detail remains collapsed"}}/>);
    expect(html).toContain('data-collapsed="true"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("reviewer");expect(html).toContain("lead");
    expect(html).not.toContain("private detail remains collapsed");
  });
});
