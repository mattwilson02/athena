<script>
  import { useThrelte } from '@threlte/core';
  import { onMount } from 'svelte';

  let { onCamera = () => {} } = $props();

  const threlte = useThrelte();

  // Export the camera ref to parent on mount and whenever it changes
  onMount(() => {
    const cam = threlte.camera?.current;
    if (cam) onCamera(cam);
  });

  // Also re-export on every frame so parent always has latest camera state
  $effect(() => {
    const cam = threlte.camera?.current;
    if (cam) onCamera(cam);
  });
</script>
