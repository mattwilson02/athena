<script>
  import { useThrelte } from '@threlte/core';
  import { onMount } from 'svelte';
  import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
  import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
  import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
  import * as THREE from 'three';

  const threlte = useThrelte();

  onMount(() => {
    const renderer = threlte.renderer;
    const scene = threlte.scene;
    const camera = threlte.camera?.current;
    if (!renderer || !scene || !camera) return;

    const size = threlte.size?.current || { width: 800, height: 600 };

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));

    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(size.width, size.height),
      0.8,    // strength — subtle glow
      0.4,    // radius
      0.6,    // threshold — only brighter nodes bloom
    );
    composer.addPass(bloomPass);

    // Override Threlte's render loop
    threlte.autoRender.set(false);

    const renderLoop = () => {
      const cam = threlte.camera?.current;
      if (cam && composer.passes[0]) {
        composer.passes[0].camera = cam;
      }
      composer.render();
    };

    // Use Threlte's scheduler for the render task
    const task = threlte.scheduler.createTask('bloom-render', renderLoop, {
      stage: threlte.renderStage,
      autoInvalidate: false,
    });

    return () => {
      task.stop();
      threlte.autoRender.set(true);
      composer.dispose();
    };
  });
</script>
