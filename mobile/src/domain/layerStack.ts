import type { LayerStackNode } from './project';

export function defaultLayerStack(layers: Array<{ name: string; src: string }>): LayerStackNode[] {
  return layers.map((layer) => ({ id: layer.src, type: 'layer', name: layer.name }));
}

export function flattenLayerStack(stack: LayerStackNode[], inheritedVisible = true): Array<{ src: string; visible: boolean }> {
  return stack.flatMap((node) => {
    const visible = inheritedVisible && node.visible !== false;
    if (node.type === 'layer') return [{ src: node.id, visible }];
    return flattenLayerStack(node.children ?? [], visible);
  });
}

export function updateStackNode(stack: LayerStackNode[], id: string, patch: Partial<LayerStackNode>): LayerStackNode[] {
  return stack.map((node) => node.id === id
    ? { ...node, ...patch }
    : node.type === 'group' ? { ...node, children: updateStackNode(node.children ?? [], id, patch) } : node);
}

export function moveStackNode(stack: LayerStackNode[], id: string, offset: -1 | 1): LayerStackNode[] {
  for (let index = 0; index < stack.length; index++) {
    if (stack[index].id === id) {
      const target = index + offset;
      if (target < 0 || target >= stack.length) return stack;
      const next = [...stack]; [next[index], next[target]] = [next[target], next[index]]; return next;
    }
    const node = stack[index];
    if (node.type === 'group') {
      const children = moveStackNode(node.children ?? [], id, offset);
      if (children !== node.children) return stack.map((entry, row) => row === index ? { ...node, children } : entry);
    }
  }
  return stack;
}

export function groupStackNode(stack: LayerStackNode[], id: string, groupName: string): LayerStackNode[] {
  for (let index = 0; index < stack.length; index++) {
    const node = stack[index];
    if (node.id === id && node.type === 'layer') {
      const group: LayerStackNode = { id: `group-${crypto.randomUUID()}`, type: 'group', name: groupName, visible: true, collapsed: false, children: [node] };
      return stack.flatMap((entry, row) => row === index ? [group] : [entry]);
    }
    if (node.type === 'group') {
      const children = groupStackNode(node.children ?? [], id, groupName);
      if (children !== node.children) return stack.map((entry, row) => row === index ? { ...node, children } : entry);
    }
  }
  return stack;
}

export function ungroupStackNode(stack: LayerStackNode[], id: string): LayerStackNode[] {
  for (let index = 0; index < stack.length; index++) {
    const node = stack[index];
    if (node.id === id && node.type === 'group') return stack.flatMap((entry, row) => row === index ? entry.children ?? [] : [entry]);
    if (node.type === 'group') {
      const children = ungroupStackNode(node.children ?? [], id);
      if (children !== node.children) return stack.map((entry, row) => row === index ? { ...node, children } : entry);
    }
  }
  return stack;
}

export function collectLayerGroups(stack: LayerStackNode[]): LayerStackNode[] {
  return stack.flatMap((node) => node.type === 'group' ? [node, ...collectLayerGroups(node.children ?? [])] : []);
}

export function moveLayerIntoGroup(stack: LayerStackNode[], layerId: string, groupId: string): LayerStackNode[] {
  let moved: LayerStackNode | undefined;
  const remove = (nodes: LayerStackNode[]): LayerStackNode[] => nodes.flatMap((node) => {
    if (node.id === layerId && node.type === 'layer') { moved = node; return []; }
    return node.type === 'group' ? [{ ...node, children: remove(node.children ?? []) }] : [node];
  });
  const without = remove(stack); if (!moved) return stack;
  if (!groupId) return [...without, moved];
  const insert = (nodes: LayerStackNode[]): LayerStackNode[] => nodes.map((node) => {
    if (node.id === groupId && node.type === 'group') return { ...node, children: [...(node.children ?? []), moved!] };
    return node.type === 'group' ? { ...node, children: insert(node.children ?? []) } : node;
  });
  const next = insert(without);
  return collectLayerGroups(without).some((group) => group.id === groupId) ? next : stack;
}
